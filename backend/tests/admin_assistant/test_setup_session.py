from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, inspect
from sqlmodel import Session, select

from app.authz.deps import get_org_principal
from app.domains.admin_assistant.models import (
    AdminAssistantMessage,
    AdminAssistantSession,
)
from app.domains.admin_assistant.store import ensure_setup_session
from app.domains.base import utc_now
from app.domains.bot.models import BotSetting
from app.domains.bot.store import append_message, cleanup_messages, messages_for
from app.domains.setup.models import SetupStatus, SetupStepState
from app.models import User
from tests.api.routes.test_setup_steps import SetupTenant
from tests.api.routes.test_setup_steps import tenant as tenant
from tests.utils.migrations import migrate


def sessions(client: TestClient, tenant: SetupTenant, role: str = "admin") -> dict:
    response = client.get(
        "/api/v1/admin-assistant/sessions",
        params={"org_id": str(tenant.org_id)},
        headers=tenant.auth[role],
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_setup_session(client: TestClient, db: Session, tenant: SetupTenant) -> None:
    data = sessions(client, tenant)["data"]
    assert len(data) == 1 and data[0]["purpose"] == "setup"
    row = db.exec(
        select(AdminAssistantSession).where(
            AdminAssistantSession.org_id == tenant.org_id
        )
    ).one()
    assert row.turn_count == 0
    payload = messages_for(db, row)[0].payload
    assert isinstance(payload, dict)
    assert [card["type"] for card in payload["messages"]] == [
        "text",
        "setup_checklist",
        "setup_form",
        "setup_form",
    ]
    assert sessions(client, tenant)["data"] == data
    todo = next(
        t
        for t in client.get(
            tenant.url + "/admin/overview", headers=tenant.auth["admin"]
        ).json()["todos"]
        if t["kind"] == "setup_step"
    )
    assert todo["page"] == f"/admin/assistant?session={row.id}"
    for role in ("manager", "employee", "foreign"):
        assert (
            client.get(
                f"/api/v1/admin-assistant/sessions/{row.id}/messages",
                params={"org_id": str(tenant.org_id)},
                headers=tenant.auth[role],
            ).status_code
            == 404
        )
        assert (
            client.get(
                "/api/v1/admin-assistant/sessions",
                params={"org_id": str(tenant.org_id)},
                headers=tenant.auth[role],
            ).status_code
            == 404
        )
    other = client.get(
        "/api/v1/admin-assistant/sessions",
        params={"org_id": str(tenant.foreign_org_id)},
        headers=tenant.auth["foreign"],
    ).json()
    assert len(other["data"]) == 1 and other["data"][0]["id"] != str(row.id)
    normal = AdminAssistantSession(org_id=tenant.org_id, user_id=tenant.admin_id)
    db.add(normal)
    append_message(db, normal, "user", {"text": "最近的普通对话"})
    db.commit()
    assert sessions(client, tenant)["data"][0]["id"] == str(row.id)


def test_concurrent_setup(db: Session, tenant: SetupTenant) -> None:
    principal = get_org_principal(tenant.org_id, db.get(User, tenant.admin_id), db)

    def ensure() -> None:
        with Session(db.get_bind()) as connection:
            ensure_setup_session(connection, principal)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: ensure(), range(2)))
    rows = db.exec(
        select(AdminAssistantSession).where(
            AdminAssistantSession.org_id == tenant.org_id
        )
    ).all()
    assert len(rows) == 1
    assert len(messages_for(db, rows[0])) == 1


@pytest.mark.parametrize("days", [0, 7])
def test_setup_retention(
    client: TestClient, db: Session, tenant: SetupTenant, days: int
) -> None:
    sessions(client, tenant)
    row = db.exec(
        select(AdminAssistantSession).where(
            AdminAssistantSession.org_id == tenant.org_id
        )
    ).one()
    db.add(BotSetting(org_id=tenant.org_id, retention_days=days))
    old = utc_now() - timedelta(days=8)
    row.last_activity_at = old
    db.add(row)
    message = db.exec(
        select(AdminAssistantMessage).where(
            AdminAssistantMessage.org_id == tenant.org_id
        )
    ).one()
    message.created_at = old
    db.add(message)
    db.commit()
    cleanup_messages(db, utc_now())
    assert sessions(client, tenant)["data"][0]["id"] == str(row.id)
    assert len(messages_for(db, row)) == 1
    append_message(db, row, "user", {"text": "回来继续"})
    db.commit()
    assert len(messages_for(db, row)) == 2
    row.last_activity_at = old
    for message in db.exec(
        select(AdminAssistantMessage).where(
            AdminAssistantMessage.org_id == tenant.org_id
        )
    ).all():
        message.created_at = old
        db.add(message)
    db.add(row)
    db.add(
        SetupStepState(
            org_id=tenant.org_id,
            step_key="model",
            status=SetupStatus.SKIPPED,
            updated_by_user_id=tenant.admin_id,
        )
    )
    db.commit()
    cleanup_messages(db, utc_now())
    assert messages_for(db, row) == []
    assert sessions(client, tenant)["data"] == []


def test_completed_no_setup(
    client: TestClient, db: Session, tenant: SetupTenant
) -> None:
    db.add(
        SetupStepState(
            org_id=tenant.org_id,
            step_key="model",
            status=SetupStatus.SKIPPED,
            updated_by_user_id=tenant.admin_id,
        )
    )
    db.commit()
    assert sessions(client, tenant)["data"] == []


def test_setup_session_migration(migration_engine: Engine) -> None:
    # Removing purpose preserves sessions and messages, including a setup conversation.
    from uuid import uuid4

    from app.domains.org.models import Organization, OrgMembership

    with Session(migration_engine) as db:
        org = Organization(slug=str(uuid4()), name="合成迁移企业")
        user = User(username=str(uuid4()), hashed_password="unused")
        db.add_all([org, user])
        db.flush()
        db.add(OrgMembership(org_id=org.id, user_id=user.id))
        db.flush()
        row = AdminAssistantSession(org_id=org.id, user_id=user.id, purpose="setup")
        db.add(row)
        db.flush()
        append_message(
            db, row, "assistant", {"messages": [{"type": "setup_checklist"}]}
        )
        db.commit()
        row_id = row.id
    migrate(migration_engine, "downgrade", "b361001dc001")
    assert "purpose" not in {
        c["name"]
        for c in inspect(migration_engine).get_columns("admin_assistant_session")
    }
    migrate(migration_engine, "upgrade", "head")
    with Session(migration_engine) as db:
        row = db.get(AdminAssistantSession, row_id)
        assert row is not None and row.purpose is None
        assert len(messages_for(db, row)) == 1
