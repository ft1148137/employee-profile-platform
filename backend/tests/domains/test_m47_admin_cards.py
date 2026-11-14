from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.authz.deps import get_org_principal
from app.authz.principal import Principal
from app.domains.admin_assistant import change_set as c
from app.domains.audit.models import AuditEvent
from app.domains.base import utc_now
from app.domains.capability.department import (
    SetDepartmentCapabilities,
    set_department_capabilities,
)
from app.domains.capability.service import read_editor
from app.domains.org.models import Department, OrgMembership
from app.models import User
from tests.admin_assistant.test_change_set import proposal
from tests.admin_assistant.test_registry import principal


@pytest.fixture
def target(db: Session, demo: tuple) -> Principal:
    user = User(username=f"{uuid4()}@example.com", hashed_password="synthetic")
    db.add(user)
    db.flush()
    org_id = principal(db, demo).org_id
    db.add(OrgMembership(org_id=org_id, user_id=user.id, employee_id=demo[1]["E03"].id))
    db.commit()
    return get_org_principal(org_id, user, db)


@pytest.mark.parametrize("kind", ["both", "add", "remove", "unchanged"])
def test_department_card_names_and_changes(db: Session, demo: tuple, kind: str) -> None:
    p = principal(db, demo)
    department = db.get_one(Department, demo[1]["E02"].department_id)
    added, removed = read_editor(db, p).definitions[:2]
    before = [] if kind == "add" else [removed.key]
    after = {
        "both": [added.key],
        "add": [added.key],
        "remove": [],
        "unchanged": [removed.key],
    }[kind]
    set_department_capabilities(
        db,
        p,
        SetDepartmentCapabilities(department_id=department.id, capability_keys=before),
    )
    row = proposal(
        db,
        demo,
        items=[
            {
                "function": "set_department_capabilities",
                "args": {
                    "department_id": str(department.id),
                    "capability_keys": after,
                },
            },
            {
                "function": "update_update_settings",
                "args": {"reminder_schedule": "yearly"},
            },
        ],
    )
    impact = c.public(row).items[0].impact
    assert impact is not None
    assert (
        impact.text
        == {
            "both": f"{department.name}：新增 {added.name}；移除 {removed.name}",
            "add": f"{department.name}：新增 {added.name}",
            "remove": f"{department.name}：移除 {removed.name}",
            "unchanged": "",
        }[kind]
    )


@pytest.mark.parametrize("expired", [False, True])
def test_admin_grant_two_confirmations(
    db: Session, demo: tuple, client: TestClient, expired: bool, target: Principal
) -> None:
    p = principal(db, demo)
    row = proposal(
        db,
        demo,
        items=[
            {
                "function": "grant_role",
                "args": {"user_id": str(target.user_id), "role": "org_admin"},
            }
        ],
    )
    path = demo[0] + f"/admin-assistant/change-sets/{row.id}"
    headers = demo[2]["admin"]
    first = client.post(
        path + "/apply", headers=headers, json={"revision": row.revision}
    )
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "draft"
    assert first.json()["confirmed_once_at"]
    assert not get_org_principal(
        p.org_id, db.get_one(User, target.user_id), db
    ).is_org_admin
    if expired:
        db.refresh(row)
        row.confirmed_once_at = utc_now() - timedelta(minutes=5)
        db.add(row)
        db.commit()
        again = client.post(
            path + "/apply", headers=headers, json={"revision": row.revision}
        )
        assert again.status_code == 200, again.text
        assert again.json()["status"] == "draft"
        assert not get_org_principal(
            p.org_id, db.get_one(User, target.user_id), db
        ).is_org_admin
    second = client.post(
        path + "/apply", headers=headers, json={"revision": first.json()["revision"]}
    )
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "applied"
    assert client.get(path, headers=headers).json()["status"] == "applied"
    assert get_org_principal(
        p.org_id, db.get_one(User, target.user_id), db
    ).is_org_admin
    events = db.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == p.org_id,
            AuditEvent.action == "role.granted",
        )
    ).all()
    event = next(e for e in events if e.details.get("change_set_id") == str(row.id))
    assert event.details["user_id"] == str(target.user_id)
    assert event.details["via"] == "admin_assistant"


@pytest.mark.parametrize("role", ["manager", "employee", "foreign"])
def test_confirmation_denied(
    db: Session, demo: tuple, client: TestClient, role: str
) -> None:
    target = principal(db, demo, "employee")
    row = proposal(
        db,
        demo,
        items=[
            {
                "function": "grant_role",
                "args": {"user_id": str(target.user_id), "role": "org_admin"},
            }
        ],
    )
    response = client.post(
        demo[0] + f"/admin-assistant/change-sets/{row.id}/apply",
        headers=demo[2][role],
        json={"revision": row.revision},
    )
    assert response.status_code == 404
    assert not principal(db, demo, "employee").is_org_admin
