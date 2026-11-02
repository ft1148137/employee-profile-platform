import csv
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from pathlib import Path
from urllib.parse import quote
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.domains.audit.models import AuditEvent
from app.domains.base import utc_now
from app.domains.capability.models import CapabilitySchemaVersion
from app.domains.org.models import (
    Department,
    Employee,
    EmploymentStatus,
    Organization,
    OrgMembership,
    ReportingLine,
    RosterImport,
)
from app.domains.org.roster import service
from app.domains.org.roster.parser import parse_roster
from app.domains.profile.models import ProfileVersion
from app.domains.setup.steps import check_roster
from app.main import app
from app.models import User
from tests.api.routes.test_setup_steps import SetupTenant
from tests.api.routes.test_setup_steps import tenant as roster_tenant

tenant = roster_tenant

SAMPLE = Path(__file__).resolve().parents[4] / "examples/roster/sample-30.csv"


@pytest.fixture
def client(tenant: SetupTenant) -> Generator[TestClient]:
    with TestClient(app, headers=tenant.auth["admin"]) as authenticated:
        yield authenticated


def upload(
    client: TestClient, tenant: SetupTenant, content: bytes | None = None
) -> dict:
    response = client.post(
        f"{tenant.url}/roster/imports",
        files={
            "file": (
                "合成名单.csv",
                content if content is not None else SAMPLE.read_bytes(),
                "text/csv",
            )
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def apply(
    client: TestClient, tenant: SetupTenant, preview: dict, **kwargs: object
) -> httpx.Response:
    return client.post(
        f"{tenant.url}/roster/imports/{preview['id']}/apply",
        json={"base_revision": preview["base_revision"], **kwargs},
    )


def records(db: Session, tenant: SetupTenant, model: type) -> list:
    db.expire_all()
    return list(db.exec(select(model).where(model.org_id == tenant.org_id)).all())


def employees(db: Session, tenant: SetupTenant) -> dict[str, Employee]:
    return {e.employee_code: e for e in records(db, tenant, Employee)}


def count(db: Session, tenant: SetupTenant, model: type) -> int:
    return len(records(db, tenant, model))


def csv_bytes(rows: list[dict[str, str]]) -> bytes:
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8-sig")


def sample_rows() -> list[dict[str, str]]:
    return list(csv.DictReader(StringIO(SAMPLE.read_text(encoding="utf-8-sig"))))


def link(db: Session, tenant: SetupTenant, employee: Employee) -> User:
    user = User(
        username=str(uuid4()),
        email=f"{uuid4()}@example.com",
        full_name=employee.display_name,
        hashed_password="unused",
    )
    db.add(user)
    db.flush()
    db.add(
        OrgMembership(org_id=tenant.org_id, user_id=user.id, employee_id=employee.id)
    )
    db.commit()
    return user


def test_first_import_history_audit_and_setup(
    tenant: SetupTenant, client: TestClient, db: Session
) -> None:
    assert check_roster(db, tenant.org_id)
    preview = upload(client, tenant)
    assert preview["can_apply"] and preview["base_revision"] == 0
    assert preview["summary"]["added"] == 30
    assert len(preview["created_departments"]) == 15
    assert count(db, tenant, Employee) == 0
    response = apply(client, tenant, preview)
    assert response.status_code == 200, response.text
    assert response.json()["summary"] == preview["summary"]
    people = employees(db, tenant)
    assert len(people) == 30 and sum(e.work_email is None for e in people.values()) == 3
    # The fixture contributes one unused department.
    assert count(db, tenant, Department) == 16
    departments = records(db, tenant, Department)
    for name, total in (("质量组", 2), ("中心办公室", 3)):
        matches = [d for d in departments if d.name == name]
        assert (
            len(matches) == total
            and len({d.parent_department_id for d in matches}) == total
        )
    assert count(db, tenant, ReportingLine) == 29
    assert check_roster(db, tenant.org_id) == []
    assert client.post(f"{tenant.url}/setup/steps/roster/complete").status_code == 200
    assert db.get(Organization, tenant.org_id).roster_revision == 1
    event = next(
        e for e in records(db, tenant, AuditEvent) if e.action == "roster.imported"
    )
    assert event.target_type == "roster_import" and event.target_id == preview["id"]
    assert event.details == {"id": preview["id"], **preview["summary"]}
    assert apply(client, tenant, preview).status_code == 409
    unchanged = upload(client, tenant)
    assert all(unchanged["summary"][k] == 0 for k in service.CHANGE_KINDS)
    assert apply(client, tenant, unchanged).status_code == 200


def test_update_departure_reactivation_and_optional_columns(
    tenant: SetupTenant, client: TestClient, db: Session
) -> None:
    assert apply(client, tenant, upload(client, tenant)).status_code == 200
    rows = sample_rows()
    people = employees(db, tenant)
    removed = rows.pop()
    departing = people[removed["工号"]]
    departing_account = link(db, tenant, departing)
    changing = people[rows[1]["工号"]]
    account = link(db, tenant, changing)
    schema = CapabilitySchemaVersion(org_id=tenant.org_id, version=1)
    db.add(schema)
    db.flush()
    profile = ProfileVersion(
        org_id=tenant.org_id,
        subject_employee_id=departing.id,
        revision=1,
        schema_version_id=schema.id,
        snapshot={},
        published_at=utc_now(),
        published_by={},
    )
    db.add(profile)
    # Create an inactive employee whose account must remain disabled when restored.
    restored = Employee(
        org_id=tenant.org_id,
        employee_code="RESTORED",
        display_name="恢复",
        employment_status=EmploymentStatus.INACTIVE,
    )
    db.add(restored)
    db.commit()
    restored_account = link(db, tenant, restored)
    restored_account.is_active = False
    db.add(restored_account)
    db.commit()
    rows[1].update(
        {
            "姓名": "合成新姓名",
            "邮箱": f"{uuid4()}@example.com",
            "部门": "星衍科技/新增部门/研发组",
            "职位": "新职位",
        }
    )
    # Change one manager and clear another.
    rows[2]["上级工号"] = rows[1]["工号"]
    rows[3]["上级工号"] = ""
    rows.extend(
        csv.DictReader(
            StringIO(
                "工号,姓名,部门,职位,上级工号,邮箱\n"
                f"NEW,新增,星衍科技/新增部门,工程师,{rows[0]['工号']},\n"
                "RESTORED,恢复,星衍科技/新增部门,,NEW,\n"
            )
        )
    )
    preview = upload(client, tenant, csv_bytes(rows))
    assert all(preview["summary"][key] >= 1 for key in service.CHANGE_KINDS)
    result = apply(client, tenant, preview)
    assert result.status_code == 200, result.text
    assert any("仍然停用" in i["message"] for i in result.json()["issues"])
    people = employees(db, tenant)
    db.refresh(account)
    db.refresh(departing_account)
    db.refresh(restored_account)
    assert account.full_name == rows[1]["姓名"] and account.email == rows[1]["邮箱"]
    assert people[changing.employee_code].title == "新职位"
    assert people[removed["工号"]].employment_status == EmploymentStatus.INACTIVE
    assert not departing_account.is_active and db.get(ProfileVersion, profile.id)
    assert people["RESTORED"].employment_status == EmploymentStatus.ACTIVE
    assert not restored_account.is_active
    lines = records(db, tenant, ReportingLine)
    current = {
        line.employee_id: line.manager_employee_id
        for line in lines
        if line.valid_to is None
    }
    assert departing.id not in current
    switched = people[rows[2]["工号"]]
    assert current[switched.id] == changing.id
    assert any(line.employee_id == switched.id and line.valid_to for line in lines)
    optional = [{k: r[k] for k in ("工号", "姓名", "部门")} for r in rows]
    assert (
        apply(client, tenant, upload(client, tenant, csv_bytes(optional))).status_code
        == 200
    )
    db.refresh(changing)
    db.refresh(account)
    assert changing.title == "新职位"
    assert changing.work_email == account.email == rows[1]["邮箱"]
    assert count(db, tenant, ReportingLine) == len(lines)


def test_recompute_email_and_admin_blockers(
    tenant: SetupTenant, client: TestClient, db: Session
) -> None:
    rows = sample_rows()
    assert apply(client, tenant, upload(client, tenant)).status_code == 200
    employee = employees(db, tenant)[rows[1]["工号"]]
    account = link(db, tenant, employee)
    previous = account.email
    rows[1]["邮箱"] = f"{uuid4()}@example.com"
    preview = upload(client, tenant, csv_bytes(rows))
    # Account ownership changes do not increment the roster revision.
    foreign_user = db.get(User, tenant.admin_id)
    foreign_user.email = rows[1]["邮箱"].upper()
    db.add(foreign_user)
    db.commit()
    result = apply(client, tenant, preview)
    assert result.status_code == 200
    assert any("不会同步" in i["message"] for i in result.json()["issues"])
    db.refresh(account)
    db.refresh(employee)
    assert account.email == previous and employee.work_email == rows[1]["邮箱"]
    # Preview is allowed before the admin becomes linked to an omitted employee.
    smaller = rows[:-1]
    allowed = upload(client, tenant, csv_bytes(smaller))
    admin_member = next(
        m for m in records(db, tenant, OrgMembership) if m.user_id == tenant.admin_id
    )
    admin_member.employee_id = employees(db, tenant)[rows[-1]["工号"]].id
    db.add(admin_member)
    db.commit()
    assert apply(client, tenant, allowed).status_code == 422
    blocked = upload(client, tenant, csv_bytes(smaller))
    assert not blocked["can_apply"] and any(
        i["severity"] == "error" for i in blocked["issues"]
    )
    assert apply(client, tenant, blocked).status_code == 422


@pytest.mark.parametrize("removed,confirm", [(6, False), (7, True)])
def test_mass_confirmation(
    tenant: SetupTenant, client: TestClient, db: Session, removed: int, confirm: bool
) -> None:
    assert apply(client, tenant, upload(client, tenant)).status_code == 200
    rows = sample_rows()[:-removed]
    remaining = {r["工号"] for r in rows}
    for row in rows:
        if row["上级工号"] not in remaining:
            row["上级工号"] = ""
    preview = upload(client, tenant, csv_bytes(rows))
    assert preview["requires_mass_deactivation_confirm"] == confirm
    response = apply(client, tenant, preview)
    if confirm:
        assert response.status_code == 422
        assert response.json()["detail"] == {
            "code": "mass_deactivation_confirm_required",
            "message": "本次导入会停用 7 名员工，请确认后再导入",
        }
        assert len(employees(db, tenant)) == 30
        response = apply(client, tenant, preview, confirm_mass_deactivation=True)
    assert response.status_code == 200
    assert (
        sum(
            e.employment_status == EmploymentStatus.INACTIVE
            for e in employees(db, tenant).values()
        )
        == removed
    )


def test_stale_concurrent_and_history(
    tenant: SetupTenant, client: TestClient, db: Session
) -> None:
    url = f"{tenant.url}/roster"
    previews = [upload(client, tenant), upload(client, tenant)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda p: apply(client, tenant, p), previews))
    assert sorted(r.status_code for r in responses) == [200, 409]
    stale = previews[next(i for i, r in enumerate(responses) if r.status_code == 409)]
    assert apply(client, tenant, stale).status_code == 409
    fresh = upload(client, tenant)
    history = client.get(f"{url}/imports").json()
    item = history["data"][0]
    assert item["filename"] == "合成名单.csv"
    assert item["uploaded_by_username"] == db.get(User, tenant.admin_id).username
    assert history["count"] == 3 and history["data"][0]["id"] == fresh["id"]
    assert {r["status"] for r in history["data"]} == {"applied", "previewed", "stale"}
    page = client.get(f"{url}/imports?skip=1&limit=1").json()
    assert page["count"] == 3 and len(page["data"]) == 1
    assert apply(client, tenant, fresh, base_revision=0).status_code == 409
    assert len(employees(db, tenant)) == 30


def test_errors_and_limits(
    tenant: SetupTenant,
    client: TestClient,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = f"{tenant.url}/roster"

    def unexpected_diff(*_args: object) -> None:
        pytest.fail("Invalid input must not be diffed")

    with monkeypatch.context() as patch:
        patch.setattr(service, "compute_roster_diff", unexpected_diff)
        preview = upload(
            client, tenant, "工号,姓名,部门,上级工号\nA,甲,部门,MISSING\n".encode()
        )
        assert (
            not preview["can_apply"]
            and not preview["changes"]
            and not preview["departments"]
        )
        assert all(v == 0 for v in preview["summary"].values())
        assert apply(client, tenant, preview).status_code == 422
    response = client.post(
        f"{url}/imports",
        files={"file": ("large.csv", b"x" * (5 * 1024 * 1024 + 1))},
    )
    assert (
        response.status_code == 413
        and response.json()["detail"] == "文件超过 5 MB，请拆分后再上传"
    )
    assert count(db, tenant, RosterImport) == 1


@pytest.mark.parametrize(
    "format,media",
    [
        ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("csv", "text/csv"),
    ],
)
def test_templates(
    tenant: SetupTenant, client: TestClient, format: str, media: str
) -> None:
    url = f"{tenant.url}/roster"
    response = client.get(f"{url}/template?format={format}")
    assert response.status_code == 200 and response.headers["content-type"].startswith(
        media
    )
    filename = f"员工名单模板.{format}"
    assert (
        response.headers["content-disposition"]
        == f"attachment; filename*=UTF-8''{quote(filename, safe='')}"
    )
    assert not parse_roster(response.content, filename).has_errors
    assert client.get(f"{url}/template?format=xls").status_code == 422


@pytest.mark.parametrize(
    "role,status", [("manager", 403), ("employee", 403), ("foreign", 404)]
)
def test_permissions(
    tenant: SetupTenant, client: TestClient, role: str, status: int
) -> None:
    urls = f"{tenant.url}/roster"
    auth = tenant.auth[role]
    responses = [
        client.get(f"{urls}/template", headers=auth),
        client.get(f"{urls}/imports", headers=auth),
        client.post(
            f"{urls}/imports",
            headers=auth,
            files={"file": ("file.csv", SAMPLE.read_bytes())},
        ),
        client.post(
            f"{urls}/imports/{uuid4()}/apply", headers=auth, json={"base_revision": 0}
        ),
    ]
    assert all(r.status_code == status for r in responses)


def test_atomic_rollback(
    tenant: SetupTenant,
    client: TestClient,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert apply(client, tenant, upload(client, tenant)).status_code == 200
    rows = sample_rows()
    people = employees(db, tenant)
    linked = link(db, tenant, people[rows[-1]["工号"]])
    rows.pop()
    rows[1]["部门"] = "回滚企业/新部门"
    rows[1]["上级工号"] = ""
    rows[1]["姓名"] = "回滚姓名"
    preview = upload(client, tenant, csv_bytes(rows))
    models = (
        Department,
        Employee,
        ReportingLine,
        RosterImport,
        Organization,
        AuditEvent,
        User,
    )

    def snapshot() -> dict:
        db.expire_all()
        return {
            m.__name__: [
                r.model_dump(mode="json")
                for r in db.exec(select(m).order_by(m.id)).all()
            ]
            for m in models
        }

    before = snapshot()

    def fail_audit(session: Session, **_kwargs: object) -> None:
        session.flush()
        raise RuntimeError("Synthetic transaction failure")

    monkeypatch.setattr(service, "record_audit", fail_audit)
    with pytest.raises(RuntimeError, match="Synthetic transaction failure"):
        apply(client, tenant, preview)
    assert snapshot() == before
    db.refresh(linked)
    assert (
        linked.is_active
        and db.get(RosterImport, UUID(preview["id"])).status == "previewed"
    )
