from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import event
from sqlmodel import Session, select

from app.core.db import engine
from app.domains.audit.models import ActorKind, AuditEvent
from app.domains.audit.service import list_events
from app.domains.base import utc_now
from app.domains.capability.models import CapabilitySchemaVersion
from app.domains.capability.templates import get_template
from app.domains.org.models import OrgMembership, Role, RoleGrant, RosterImport
from tests.authz.test_tenant_access import headers, tenant_data  # noqa: F401


@pytest.fixture
def audit_data(
    request: pytest.FixtureRequest, db: Session, client: TestClient
) -> tuple:
    orgs, depts, _, users = request.getfixturevalue("tenant_data")
    url = f"/api/v1/orgs/{orgs[0].id}"

    def add(
        action: str = "test",
        kind: str = "user",
        target: object = None,
        details: dict | None = None,
        actor: str | None = None,
        actor_type: ActorKind = ActorKind.USER,
        org_id: UUID | None = orgs[0].id,
    ) -> AuditEvent:
        e = AuditEvent(
            org_id=org_id,
            actor_type=actor_type,
            actor_id=actor or str(users[0].id),
            action=action,
            target_type=kind,
            target_id=str(target),
            details=details or {},
        )
        db.add(e)
        db.flush()
        return e

    def get(**params: str | int) -> Response:
        db.commit()
        return client.get(
            f"{url}/audit-events", headers=headers(users[0]), params=params
        )

    return orgs, depts, users, url, add, get


def test_filters_and_permissions(
    audit_data: tuple, db: Session, client: TestClient
) -> None:
    orgs, _, users, url, add, get = audit_data
    now = utc_now()
    events = []
    for i, action in enumerate(
        [
            "account.issued",
            "account.enabled",
            "role.granted",
            "a%.test",
            "ax.test",
            "a_.test",
            "ab.test",
        ]
    ):
        e = add(action)
        e.created_at = now + timedelta(seconds=i // 2)
        events.append(e)
    add("account.issued", org_id=orgs[1].id)
    platform = add("account.issued", org_id=None)
    events.append(add("cli", actor_type=ActorKind.CLI))
    membership = db.exec(
        select(OrgMembership).where(OrgMembership.user_id == users[3].id)
    ).one()
    db.add(
        RoleGrant(org_id=orgs[1].id, membership_id=membership.id, role=Role.ORG_ADMIN)
    )
    result = get().json()
    assert result["count"] == 8
    assert [e["id"] for e in result["data"]] == [
        str(e.id)
        for e in sorted(events, key=lambda e: (e.created_at, e.id), reverse=True)
    ]
    assert get(skip=2, limit=2).json() == {"data": result["data"][2:4], "count": 8}
    for action, count in (
        ("account.issued", 1),
        ("account.", 2),
        ("a%.", 1),
        ("a_.", 1),
    ):
        assert get(action=action).json()["count"] == count
    assert get(actor_user_id=str(users[0].id)).json()["count"] == 7
    assert get(actor_user_id=str(users[2].id)).json()["count"] == 0
    assert (
        get(
            action="account.",
            since=now.isoformat(),
            until=(now + timedelta(seconds=1)).isoformat(),
        ).json()["count"]
        == 2
    )
    for params in (
        {"since": "2026-01-01T00:00:00"},
        {"until": "2026-01-01T00:00:00"},
        {"skip": -1},
        {"limit": 0},
        {"limit": 101},
    ):
        assert get(**params).status_code == 422
    for index, status in ((1, 403), (2, 403), (3, 404)):
        assert (
            client.get(f"{url}/audit-events", headers=headers(users[index])).status_code
            == status
        )
    assert client.get(f"{url}/audit-events").status_code == 401
    db.delete(platform)
    db.commit()


def test_labels_and_role_details(
    audit_data: tuple, db: Session, client: TestClient
) -> None:
    orgs, depts, users, url, add, get = audit_data
    users[2].full_name = "合成员工"
    depts[1].parent_department_id = depts[0].id
    db.add_all([users[2], depts[1]])
    db.commit()
    auth = headers(users[0])
    granted = client.post(
        f"{url}/role-grants",
        headers=auth,
        json={
            "user_id": str(users[2].id),
            "role": "dept_manager",
            "department_id": str(depts[1].id),
        },
    )
    assert granted.status_code == 200
    grant_id = granted.json()["id"]
    old_id = add(kind="role_grant", target=grant_id, details={"id": grant_id}).id
    expected = f"合成员工（{users[2].username}） · 部门经理 · {depts[0].name} / {depts[1].name}"
    assert (
        next(e for e in get().json()["data"] if e["id"] == str(old_id))["target_label"]
        == expected
    )
    assert (
        client.delete(f"{url}/role-grants/{grant_id}", headers=auth).status_code == 200
    )
    result = {e["id"]: e for e in get().json()["data"]}
    assert result[str(old_id)]["target_label"] is None
    for e in result.values():
        if e["action"] in ("role.granted", "role.revoked"):
            assert e["target_label"] == expected
            assert e["details"] == {
                "id": grant_id,
                "user_id": str(users[2].id),
                "role": "dept_manager",
                "department_id": str(depts[1].id),
            }
    schema = db.exec(
        select(CapabilitySchemaVersion).where(
            CapabilitySchemaVersion.org_id == orgs[0].id
        )
    ).one()
    imported = RosterImport(
        org_id=orgs[0].id,
        filename="合成名单.csv",
        file_sha256="test",
        rows=[],
        column_mapping={},
        issues=[],
        summary={},
        base_revision=0,
        uploaded_by_user_id=users[0].id,
    )
    db.add(imported)
    db.flush()
    targets = [
        ("user", users[2].id, {}, f"合成员工（{users[2].username}）"),
        ("user", users[3].id, {}, None),
        ("user", uuid4(), {}, None),
        ("organization", orgs[0].id, {}, orgs[0].name),
        ("organization", orgs[1].id, {}, None),
        (
            "capability_schema_version",
            schema.id,
            {"template_id": "tech-rnd"},
            f"{get_template('tech-rnd').entry.name} · 第 1 版",
        ),
        ("capability_schema_version", schema.id, {}, "第 1 版"),
        ("roster_import", imported.id, {}, "合成名单.csv"),
        ("other", "invalid-id", {}, None),
        (
            "role_grant",
            uuid4(),
            {"user_id": str(users[2].id), "role": "org_admin"},
            f"合成员工（{users[2].username}） · 企业管理员",
        ),
    ]
    labels = {
        add(kind=kind, target=target, details=details).id: label
        for kind, target, details, label in targets
    }
    missing = add(actor=str(uuid4())).id
    no_name = add(target=users[0].id).id
    result = {e["id"]: e for e in get().json()["data"]}
    assert result[str(no_name)]["target_label"] == users[0].username
    assert result[str(missing)]["actor"] is None
    for id_, label in labels.items():
        assert result[str(id_)]["target_label"] == label
    assert result[str(no_name)]["actor"] == {
        "user_id": str(users[0].id),
        "username": users[0].username,
        "full_name": users[0].full_name,
    }


def test_query_count(audit_data: tuple, db: Session) -> None:
    orgs, _, users, _, add, _ = audit_data
    org_id = orgs[0].id
    for _ in range(50):
        add("account.issued", target=users[2].id)
    db.commit()
    counts = []
    statements: list[object] = []

    def count_query(*args: object) -> None:
        statements.append(args[2])

    for limit in (5, 50):
        statements.clear()
        with Session(engine) as session:
            event.listen(engine, "before_cursor_execute", count_query)
            try:
                page = list_events(session, org_id, limit=limit)
            finally:
                event.remove(engine, "before_cursor_execute", count_query)
        assert len(page.data) == limit and page.count == 50
        counts.append(len(statements))
    assert counts == [8, 8]
