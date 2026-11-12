from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, col, delete, select

from app.authz.deps import get_org_principal
from app.domains.admin_assistant import store as admin_store
from app.domains.admin_assistant.models import AdminAssistantMessage
from app.domains.base import utc_now
from app.domains.bot import store
from app.domains.bot.models import BotMessage, BotSetting
from app.domains.org.models import OrgMembership, Role, RoleGrant
from app.domains.setup.models import SetupStatus
from app.domains.setup.wizard import update_step
from app.models import User
from tests.api.routes.test_employee_requests import principal_for
from tests.domains.test_admin_assistant import admin


@pytest.mark.parametrize("administrator", [False, True])
@pytest.mark.parametrize("days", [0, 7])
def test_recent_sessions(
    demo: tuple, db: Session, client: TestClient, administrator: bool, days: int
) -> None:
    _, people, auth = demo
    org_id = people["E02"].org_id
    p = admin(db, org_id) if administrator else principal_for(db, org_id)
    if administrator:
        update_step(db, p, "model", SetupStatus.SKIPPED)
    settings = db.get(BotSetting, org_id) or BotSetting(org_id=org_id)
    settings.retention_days = days
    db.add(settings)
    db.commit()
    create = admin_store.create_session if administrator else store.create_session
    # Another administrator in this organization must see only their own sessions.
    membership = db.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == org_id,
            OrgMembership.employee_id == people["E01"].id,
        )
    ).one()
    db.add(
        RoleGrant(
            org_id=org_id,
            membership_id=membership.id,
            role=Role.ORG_ADMIN,
            granted_by_user_id=admin(db, org_id).user_id,
        )
    )
    db.commit()
    manager = get_org_principal(org_id, db.get(User, membership.user_id), db)
    other = create(db, manager)
    store.append_message(db, other, "user", {"text": "别人的对话"})
    empty = create(db, p)
    expired = create(db, p)
    expired.turn_count = 1
    msg = store.append_message(db, expired, "user", {"text": "过期对话"})
    msg.created_at = utc_now() - timedelta(days=8)
    expired.last_activity_at = msg.created_at
    db.add(msg)
    db.add(expired)
    if not administrator:
        im = store.create_session(db, p, channel="feishu")
        store.append_message(db, im, "user", {"text": "飞书对话"})
    db.commit()
    ids = []
    for i in range(52):
        row = create(db, p)
        store.append_message(db, row, "user", {"text": str(i) + "合成消息" * 20})
        store.append_message(db, row, "assistant", {"messages": []})
        row.turn_count = i + 1
        row.last_activity_at = utc_now() - timedelta(minutes=52 - i)
        db.add(row)
        db.commit()
        ids.append(str(row.id))
    url = "/api/v1/" + ("admin-assistant/sessions" if administrator else "bot/sessions")
    query = {"org_id": str(org_id)}
    headers = auth["admin" if administrator else "employee"]
    assert (
        client.get(f"{url}/{empty.id}/messages", params=query, headers=headers).json()
        == []
    )
    assert (
        client.get(
            f"{url}/{expired.id}/messages", params=query, headers=headers
        ).status_code
        == 404
    )
    response = client.get(url, params=query, headers=headers)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["retention_days"] == days
    assert [item["id"] for item in result["data"]] == ids[::-1][:50]
    assert result["data"][0]["preview"] == ("51" + "合成消息" * 20)[:40]
    assert result["data"][0]["turn_count"] == 52
    assert str(empty.id) not in {item["id"] for item in result["data"]}
    others = client.get(url, params=query, headers=auth["manager"])
    assert [item["id"] for item in others.json()["data"]] == [str(other.id)]
    assert client.get(url, params=query, headers=auth["foreign"]).status_code == 404
    assert (
        client.get(url, params={"org_id": str(uuid4())}, headers=headers).status_code
        == 404
    )
    if administrator:
        assert (
            client.get(url, params=query, headers=auth["employee"]).status_code == 404
        )

    message_type = AdminAssistantMessage if administrator else BotMessage
    db.exec(delete(message_type).where(col(message_type.session_id).in_(ids)))
    db.commit()
    assert client.get(url, params=query, headers=headers).json()["data"] == []
    assert (
        client.get(
            f"{url}/{ids[-1]}/messages", params=query, headers=headers
        ).status_code
        == 404
    )


def test_selected_drafts(demo: tuple, db: Session, client: TestClient) -> None:
    from tests.admin_assistant.test_change_set import proposal

    first = proposal(db, demo)
    second = proposal(db, demo)
    url = demo[0] + "/admin-assistant/change-sets"
    query = {"status": "draft", "function": "update_update_settings"}
    headers = demo[2]["admin"]
    assert [r["id"] for r in client.get(url, params=query, headers=headers).json()] == [
        str(second.id)
    ]
    query["session_id"] = str(first.session_id)
    assert [r["id"] for r in client.get(url, params=query, headers=headers).json()] == [
        str(first.id)
    ]
    p = admin(db, demo[1]["E02"].org_id)
    membership = db.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == p.org_id,
            OrgMembership.employee_id == demo[1]["E01"].id,
        )
    ).one()
    db.add(
        RoleGrant(
            org_id=p.org_id,
            membership_id=membership.id,
            role=Role.ORG_ADMIN,
            granted_by_user_id=p.user_id,
        )
    )
    db.commit()
    for role in ("manager", "employee", "foreign"):
        assert client.get(url, params=query, headers=demo[2][role]).status_code == 404
    query["session_id"] = str(uuid4())
    assert client.get(url, params=query, headers=headers).status_code == 404
