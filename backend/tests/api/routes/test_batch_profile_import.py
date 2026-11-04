import json
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlmodel import Session, select

from app.domains.audit.models import AuditEvent
from app.domains.change_request.models import (
    ChangeItem,
    ProfileChangeRequest,
    RequestStatus,
)
from app.domains.evidence.models import Evidence
from app.domains.inbox.models import InboxItem
from app.domains.org.models import (
    Employee,
    EmploymentStatus,
    Organization,
    OrgMembership,
)
from app.domains.profile.models import ProfileVersion
from tests.api.routes.test_profile_import import counts, example


def upload(
    client: TestClient,
    demo: tuple,
    files: list[tuple[str, object]],
    *,
    confirm: bool = True,
    role: str = "admin",
) -> Response:
    url, _, auth = demo
    return client.post(
        f"{url}/profile-imports",
        headers=auth[role],
        params={"confirm": confirm},
        files=[
            (
                "files",
                (
                    name,
                    body if isinstance(body, bytes) else json.dumps(body).encode(),
                    "application/json",
                ),
            )
            for name, body in files
        ],
    )


def person(subject: Employee) -> dict:
    return {**example(), "employee_email": subject.work_email.upper()}


def snapshot(db: Session, org_id: UUID) -> list[list[dict]]:
    return [
        [
            row.model_dump(mode="json")
            for row in db.exec(
                select(model).where(model.org_id == org_id).order_by(model.id)
            ).all()
        ]
        for model in (ProfileChangeRequest, ChangeItem, InboxItem, AuditEvent, Evidence)
    ]


def close_initial(db: Session, subject: Employee) -> None:
    for row in db.exec(
        select(ProfileChangeRequest).where(
            ProfileChangeRequest.org_id == subject.org_id,
            ProfileChangeRequest.subject_employee_id == subject.id,
        )
    ).all():
        row.status = RequestStatus.REJECTED
        db.add(row)
    db.commit()


def test_batch_preview_create_owner_edit_and_permissions(
    demo: tuple, client: TestClient, db: Session
) -> None:
    url, employees, auth = demo
    subject = employees["E02"]
    close_initial(db, subject)
    org_id = subject.org_id
    profile = client.get(f"{url}/me/profile", headers=auth["employee"]).json()
    existing = next(i for i in profile["items"] if i["claim"])
    first = person(subject)
    first["items"].append(
        {
            **first["items"][0],
            "capability_key": existing["capability"]["key"],
            "level": None,
        }
    )
    files = [
        ("team.json", [first, person(employees["E03"])]),
        ("single.json", person(employees["E06"])),
    ]
    before = snapshot(db, org_id)
    response = upload(client, demo, files, confirm=False)
    assert response.status_code == 200, response.text
    assert [r["status"] for r in response.json()] == ["will_create"] * 3
    assert all(
        set(r) == {"display_name", "employee_email", "item_count", "status", "reason"}
        for r in response.json()
    )
    assert snapshot(db, org_id) == before
    response = upload(client, demo, files)
    assert response.status_code == 200, response.text
    assert [r["status"] for r in response.json()] == ["created"] * 3
    rows = db.exec(
        select(ProfileChangeRequest).where(
            ProfileChangeRequest.org_id == org_id,
            ProfileChangeRequest.origin == "import",
        )
    ).all()
    assert len(rows) == 3
    admin_id = client.get("/api/v1/users/me", headers=auth["admin"]).json()["id"]
    for row in rows:
        assert row.status == "pending_owner" and row.author == {
            "actor_type": "user",
            "user_id": admin_id,
        }
        notifications = db.exec(
            select(InboxItem).where(
                InboxItem.org_id == org_id, InboxItem.request_id == row.id
            )
        ).all()
        assert len(notifications) == 1
        assert notifications[0].recipient_employee_id == row.subject_employee_id
        assert notifications[0].kind == "initial_review"
        assert notifications[0].message == "管理员导入了 AI 生成的画像，请过目后提交"
    audits = db.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == org_id, AuditEvent.action == "profile_import.created"
        )
    ).all()
    assert len(audits) == 3 and all(
        a.actor_id == admin_id and a.details["filename"] in ("team.json", "single.json")
        for a in audits
    )
    assert counts(db, org_id)[1] == len(before[-1])
    request = next(
        r
        for r in client.get(f"{url}/me/requests", headers=auth["employee"]).json()[
            "data"
        ]
        if r["origin"] == "import"
    )
    assert [i["op"] for i in request["items"]] == ["add", "modify"]
    row = next(r for r in rows if r.subject_employee_id == subject.id)
    assert (
        row.base_version_id
        == db.exec(
            select(ProfileVersion.id)
            .where(
                ProfileVersion.org_id == org_id,
                ProfileVersion.subject_employee_id == subject.id,
            )
            .order_by(ProfileVersion.revision.desc())
        ).first()
    )
    request_id = request["id"]
    data = {
        "items": [
            {
                "op": i["op"],
                "capability_key": i["capability"]["key"],
                "statement": "本人核对后的陈述",
                "owner_statement": i["owner_statement"],
                "uncertainty": i["uncertainty"],
            }
            for i in request["items"]
        ]
    }
    for role in ("admin", "manager", "foreign"):
        for method, suffix, payload in (
            ("PUT", "items", data),
            ("POST", "submit", {"revision": request["revision"]}),
            ("POST", "withdraw", None),
        ):
            assert client.request(
                method,
                f"{url}/me/requests/{request_id}/{suffix}",
                headers=auth[role],
                json=payload,
            ).status_code in (403, 404)
    edited = client.put(
        f"{url}/me/requests/{request_id}/items", headers=auth["employee"], json=data
    )
    assert edited.status_code == 200, edited.text
    submitted = client.post(
        f"{url}/me/requests/{request_id}/submit",
        headers=auth["employee"],
        json={"revision": edited.json()["revision"]},
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "pending_review"
    assert all(
        i["uncertainty"] == first["items"][0]["uncertainty"]
        for i in submitted.json()["items"]
    )
    reports = db.exec(
        select(Evidence).where(
            Evidence.org_id == org_id, Evidence.source == "self_report:web"
        )
    ).all()
    assert len(reports) == 2 and all(
        r.provenance["imported_by"] == admin_id and r.visibility == "restricted"
        for r in reports
    )
    after = client.get(f"{url}/me/profile", headers=auth["employee"]).json()
    for key in ("items", "latest_published", "counts"):
        assert after[key] == profile[key]


@pytest.mark.parametrize("status", [RequestStatus.DRAFT, RequestStatus.PENDING_OWNER])
def test_existing_request_is_skipped(
    demo: tuple, client: TestClient, db: Session, status: RequestStatus
) -> None:
    _, employees, _ = demo
    subject = employees["E02"]
    initial = db.exec(
        select(ProfileChangeRequest).where(
            ProfileChangeRequest.org_id == subject.org_id,
            ProfileChangeRequest.subject_employee_id == subject.id,
        )
    ).one()
    initial.status = status
    db.add(initial)
    db.commit()
    before = snapshot(db, subject.org_id)
    response = upload(client, demo, [("one.json", person(subject))])
    assert response.status_code == 200
    assert response.json()[0]["status"] == "skipped"
    assert response.json()[0]["display_name"] == subject.display_name
    assert response.json()[0]["reason"]
    assert snapshot(db, subject.org_id) == before


def test_format_and_business_errors_reject_entire_batch(
    demo: tuple, client: TestClient, db: Session
) -> None:
    _, employees, _ = demo
    valid = person(employees["E03"])
    bad = person(employees["E06"])
    bad["items"][0]["level"] = "missing"
    missing = person(employees["E08"])
    missing["items"][0]["capability_key"] = "missing"
    before = snapshot(db, employees["E02"].org_id)
    response = upload(
        client,
        demo,
        [
            ("valid.json", valid),
            ("bad.json", [{}, bad, missing]),
            ("syntax.json", b'{\n"bad":'),
        ],
    )
    assert response.status_code == 422
    for message in (
        "bad.json 第 1 个人 employee_email",
        "bad.json 第 2 个人 第 1 项 level",
        "bad.json 第 3 个人 第 1 项 capability_key",
        "syntax.json 文件：第 2 行",
    ):
        assert message in response.text
    assert snapshot(db, employees["E02"].org_id) == before


@pytest.mark.parametrize(
    "kind",
    [
        "duplicate",
        "people_limit",
        "size",
        "empty",
        "encoding",
        "foreign",
        "inactive",
        "unknown",
        "duplicate_item",
    ],
)
def test_invalid_batch_is_atomic(
    demo: tuple, client: TestClient, db: Session, kind: str
) -> None:
    _, employees, _ = demo
    subject = employees["E03"]
    valid = person(subject)
    body: object = valid
    files: list[tuple[str, object]] = []
    message = ""
    if kind == "duplicate":
        files = [("first.json", valid)]
        body = {**valid, "employee_email": subject.work_email.lower()}
        message = "第 1 个人 employee_email：同一批里邮箱不能重复"
    elif kind == "people_limit":
        files = [("first.json", [valid] * 200)]
        message = "最多 200 人"
    elif kind == "size":
        body, message = b" " * (1024 * 1024 + 1), "1 MB"
    elif kind == "empty":
        body, message = [], "至少需要一个人"
    elif kind == "encoding":
        body, message = b"\xff", "UTF-8"
    elif kind == "duplicate_item":
        valid["items"] *= 2
        message = "第 1 个人 第 2 项 capability_key"
    else:
        if kind == "foreign":
            foreign_user_id = UUID(
                client.get("/api/v1/users/me", headers=demo[2]["foreign"]).json()["id"]
            )
            org = db.exec(
                select(Organization)
                .join(OrgMembership)
                .where(OrgMembership.user_id == foreign_user_id)
            ).one()
            db.add(
                Employee(
                    org_id=org.id,
                    display_name="Foreign secret",
                    work_email="hidden@foreign.example.com",
                )
            )
            valid["employee_email"] = "hidden@foreign.example.com"
        elif kind == "inactive":
            subject.employment_status = EmploymentStatus.INACTIVE
            db.add(subject)
        else:
            valid["employee_email"] = "unknown@foreign.example.com"
        db.commit()
        message = "第 1 个人 employee_email：不是本企业在职员工"
    before = snapshot(db, subject.org_id)
    response = upload(client, demo, [*files, ("bad.json", body)])
    assert response.status_code == 422, response.text
    assert any(
        m.startswith("bad.json ") and message in m
        for m in response.json()["detail"]["blockers"]
    )
    assert "Foreign secret" not in response.text
    assert snapshot(db, subject.org_id) == before


def test_non_admin_and_cross_tenant_are_denied(
    shared_demo: tuple, client: TestClient
) -> None:
    _, employees, _ = shared_demo
    for role, status in (("employee", 403), ("manager", 403), ("foreign", 404)):
        assert (
            upload(
                client, shared_demo, [("one.json", person(employees["E03"]))], role=role
            ).status_code
            == status
        )


def test_concurrent_import_creates_once(
    demo: tuple, client: TestClient, db: Session
) -> None:
    _, employees, _ = demo
    files = [("one.json", person(employees["E03"]))]
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: upload(client, demo, files), range(2)))
    assert all(r.status_code == 200 for r in responses)
    assert sorted(r.json()[0]["status"] for r in responses) == ["created", "skipped"]
    assert (
        len(
            db.exec(
                select(ProfileChangeRequest).where(
                    ProfileChangeRequest.org_id == employees["E03"].org_id,
                    ProfileChangeRequest.subject_employee_id == employees["E03"].id,
                )
            ).all()
        )
        == 1
    )
