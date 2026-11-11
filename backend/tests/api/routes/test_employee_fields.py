import asyncio
from datetime import date, datetime
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import Engine, text
from sqlmodel import Session, select

from app.domains.admin_assistant import change_set as c
from app.domains.admin_assistant import registry, store
from app.domains.admin_assistant.schemas import ProposeChanges
from app.domains.audit.models import AuditEvent
from app.domains.drafting.model import DraftInput
from app.domains.org.employee_fields import FIELD_LABELS
from app.domains.org.models import Organization
from app.domains.org.roster import single
from app.domains.org.roster.parser import parse_roster
from app.domains.org.schemas import EmployeeSummary
from app.domains.profile.models import ProfileVersion
from tests.admin_assistant.test_personnel import principal
from tests.api.routes.test_roster import apply, employees, upload
from tests.api.routes.test_roster import client as client
from tests.api.routes.test_setup_steps import SetupTenant
from tests.api.routes.test_setup_steps import tenant as tenant
from tests.utils.migrations import migrate

BASE = "工号,姓名,部门,职位,上级工号,邮箱"
ROW = "A,甲,研发,工程师,,a@example.com"


def switches(client: TestClient, tenant: SetupTenant, fields: list[str]) -> None:
    result = client.put(f"{tenant.url}/employee-fields", json={"fields": fields})
    assert result.status_code == 200, result.text


def test_import_visibility_and_preservation(
    db: Session, tenant: SetupTenant, client: TestClient
) -> None:
    full = f"{BASE},岗位序列,工作地点,入职日期\n{ROW},技术,上海,2020-03-04".encode()
    preview = upload(client, tenant, full)
    assert len(preview["issues"]) == 3
    assert all("未开启" in i["message"] for i in preview["issues"])
    assert apply(client, tenant, preview).status_code == 200
    employee = employees(db, tenant)["A"]
    assert employee.job_family is employee.work_location is employee.hire_date is None
    switches(client, tenant, list(FIELD_LABELS))
    preview = upload(client, tenant, full)
    assert {f["field"] for f in preview["changes"][0]["fields"]} == set(FIELD_LABELS)
    assert apply(client, tenant, preview).status_code == 200
    db.expire_all()
    expected = {
        "job_family": "技术",
        "work_location": "上海",
        "hire_date": "2020-03-04",
    }

    def account() -> dict:
        result = client.get(
            f"{tenant.url}/accounts", params={"employee_id": str(employee.id)}
        )
        assert result.status_code == 200
        return result.json()["data"][0]

    assert {f: account()[f] for f in FIELD_LABELS} == expected
    p = principal(db, tenant)
    result = asyncio.run(registry.admin_read(db, p, "list_accounts", {}))
    assert not set(FIELD_LABELS) & result["data"]["data"][0].keys()
    assert set(FIELD_LABELS) <= EmployeeSummary.model_fields.keys()
    assert not set(FIELD_LABELS) & DraftInput.model_fields.keys()
    snapshots = db.exec(
        select(ProfileVersion).where(ProfileVersion.org_id == p.org_id)
    ).all()
    assert not snapshots  # Importing work attributes never publishes a profile.
    single.apply(db, p, single.Transfer(employee_id=employee.id, title="架构师"))
    db.expire_all()
    assert {f: account()[f] for f in FIELD_LABELS} == expected
    # Missing columns preserve values; present empty cells clear them.
    assert (
        apply(
            client, tenant, upload(client, tenant, f"{BASE}\n{ROW}".encode())
        ).status_code
        == 200
    )
    assert {f: account()[f] for f in FIELD_LABELS} == expected
    switches(client, tenant, [])
    assert all(account()[f] is None for f in FIELD_LABELS)
    db.refresh(employee)
    assert employee.job_family == "技术" and employee.hire_date == date(2020, 3, 4)
    assert employee.work_location == "上海"
    switches(client, tenant, list(FIELD_LABELS))
    assert {f: account()[f] for f in FIELD_LABELS} == expected
    clear = f"{BASE},岗位序列,工作地点,入职日期\n{ROW},,,".encode()
    assert apply(client, tenant, upload(client, tenant, clear)).status_code == 200
    db.refresh(employee)
    assert employee.job_family is employee.work_location is employee.hire_date is None


def test_switch_validation_audit_and_stale_previews(
    db: Session, tenant: SetupTenant, client: TestClient
) -> None:
    assert (
        client.put(
            f"{tenant.url}/employee-fields", json={"fields": ["phone"]}
        ).status_code
        == 422
    )
    preview = upload(client, tenant, f"{BASE}\n{ROW}".encode())
    assert apply(client, tenant, preview).status_code == 200
    employee = employees(db, tenant)["A"]
    p = principal(db, tenant)
    single_preview = single.preview(
        db, p, single.Transfer(employee_id=employee.id, title="新职位")
    )
    file_preview = upload(client, tenant, f"{BASE}\n{ROW}".encode())
    change = c.propose(
        db,
        p,
        store.create_session(db, p).id,
        ProposeChanges.model_validate(
            {
                "items": [
                    {
                        "function": "transfer_employee",
                        "args": {"employee_id": str(employee.id), "title": "新职位"},
                    }
                ]
            }
        ),
    )
    db.commit()
    org = db.get_one(Organization, tenant.org_id)
    revision = org.roster_revision
    switches(client, tenant, ["hire_date", "job_family", "hire_date"])
    switches(client, tenant, ["job_family", "hire_date"])
    db.refresh(org)
    assert org.employee_fields == ["job_family", "hire_date"]
    assert org.roster_revision == revision + 1
    events = db.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == tenant.org_id,
            AuditEvent.action == "org.employee_fields_updated",
        )
    ).all()
    assert len(events) == 1
    assert events[0].details == {"before": [], "after": ["job_family", "hire_date"]}
    for preview in (file_preview, single_preview.model_dump(mode="json")):
        assert apply(client, tenant, preview).status_code == 409
    with pytest.raises(c.ChangeSetError) as error:
        c.apply(db, p, change.id, change.revision)
    assert error.value.status_code == 409
    db.refresh(employee)
    assert employee.title == "工程师"
    assert (
        client.put(
            f"{tenant.url}/employee-fields",
            headers=tenant.auth["foreign"],
            json={"fields": []},
        ).status_code
        == 404
    )
    for role in ("employee", "manager"):
        assert (
            client.put(
                f"{tenant.url}/employee-fields",
                headers=tenant.auth[role],
                json={"fields": []},
            ).status_code
            == 403
        )


def test_assistant_switch_confirmation_and_undo(
    db: Session, tenant: SetupTenant
) -> None:
    p = principal(db, tenant)
    org = db.get_one(Organization, tenant.org_id)
    revision = org.roster_revision
    row = c.propose(
        db,
        p,
        store.create_session(db, p).id,
        ProposeChanges.model_validate(
            {
                "items": [
                    {
                        "function": "update_employee_fields",
                        "args": {"fields": ["hire_date"]},
                    }
                ]
            }
        ),
    )
    db.commit()
    card = c.public(row)
    assert card.items[0].before == {"fields": []}
    assert card.items[0].after == {"fields": ["hire_date"]}
    assert org.employee_fields == []
    c.apply(db, p, row.id, row.revision)
    db.refresh(org)
    assert org.employee_fields == ["hire_date"] and org.roster_revision == revision + 1
    reverse = c.undo(db, p, row.id)
    assert reverse.status == "applied"
    db.refresh(org)
    assert org.employee_fields == [] and org.roster_revision == revision + 2


@pytest.mark.parametrize(
    "value,valid",
    [
        ("2020-01-02", True),
        (datetime(2020, 1, 2), True),
        ("2020/01/02", False),
        ("2020-02-30", False),
        (43832, False),
        ("=1+1", False),
    ],
)
def test_xlsx_dates(value: object, valid: bool) -> None:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append([*BASE.split(","), "入职日期"])
    sheet.append([*ROW.split(","), value])
    content = BytesIO()
    workbook.save(content)
    workbook.close()
    result = parse_roster(content.getvalue(), "名单.xlsx", ["hire_date"])
    assert not parse_roster(content.getvalue(), "名单.xlsx").has_errors
    assert result.has_errors != valid
    if valid:
        assert result.rows[0].hire_date == date(2020, 1, 2)
    else:
        assert (
            result.issues[0].row_number == 2 and result.issues[0].column == "入职日期"
        )


def test_optional_field_validation() -> None:
    for column, value in (
        ("岗位序列", "字" * 51),
        ("工作地点", "字" * 51),
        ("入职日期", "bad"),
    ):
        content = f"{BASE},{column}\n{ROW},{value}".encode()
        assert parse_roster(content, "名单.csv", list(FIELD_LABELS)).has_errors
        assert not parse_roster(content, "名单.csv").has_errors


def test_employee_fields_migration(migration_engine: Engine) -> None:
    migrate(migration_engine, "downgrade", "b283001bc001")
    with migration_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO organization (id, created_at, slug, name) VALUES (gen_random_uuid(), now(), 'fields', 'Synthetic')"
            )
        )
    migrate(migration_engine, "upgrade", "head")
    with migration_engine.begin() as connection:
        assert (
            connection.execute(
                text("SELECT employee_fields FROM organization WHERE slug='fields'")
            ).scalar_one()
            == []
        )
    migrate(migration_engine, "downgrade", "b283001bc001")
    with migration_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM information_schema.columns WHERE table_name='employee' AND column_name IN ('job_family','work_location','hire_date')"
                )
            ).scalar_one()
            == 0
        )
    migrate(migration_engine, "upgrade", "head")


def test_profile_snapshots_and_drafting_exclude_work_fields(
    db: Session, demo: tuple
) -> None:
    import json

    employee = demo[1]["E02"]
    org = db.get_one(Organization, employee.org_id)
    org.employee_fields = list(FIELD_LABELS)
    db.add(org)
    versions = db.exec(
        select(ProfileVersion).where(ProfileVersion.org_id == employee.org_id)
    ).all()
    assert versions
    before = [version.snapshot for version in versions]
    from app.domains.admin_assistant import people
    from app.domains.org.models import OrgMembership
    from tests.admin_assistant.test_registry import principal as demo_principal

    member = db.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == employee.org_id,
            OrgMembership.employee_id == employee.id,
        )
    ).one()
    snapshot = people.account_snapshot(
        db, demo_principal(db, demo), people.AccountArgs(user_id=member.user_id)
    ).model_dump()
    assert not set(FIELD_LABELS) & snapshot["accounts"][0]["employee"].keys()
    employee.job_family, employee.work_location, employee.hire_date = (
        "技术",
        "上海",
        date(2020, 1, 2),
    )
    db.add(employee)
    db.commit()
    db.expire_all()
    assert [version.snapshot for version in versions] == before
    for field in FIELD_LABELS:
        assert field not in json.dumps(before)
        assert field not in json.dumps(DraftInput.model_json_schema())
