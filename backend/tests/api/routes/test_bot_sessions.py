from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, col, delete, select

from app.authz.deps import get_org_principal
from app.domains.admin_assistant import store as admin_store
from app.domains.admin_assistant.models import AdminAssistantMessage
from app.domains.base import utc_now
from app.domains.bot import schemas as s
from app.domains.bot import store, tools
from app.domains.bot.models import BotMessage, BotSetting
from app.domains.change_request import employee
from app.domains.change_request.models import ProfileChangeRequest, RequestStatus
from app.domains.change_request.schemas import ItemInput
from app.domains.org.models import OrgMembership, Role, RoleGrant
from app.domains.review.models import ApprovalPolicy
from app.domains.setup.models import SetupStatus
from app.domains.setup.wizard import update_step
from app.models import User
from tests.api.routes.test_employee_requests import payload, principal_for
from tests.domains.test_admin_assistant import admin


@pytest.mark.parametrize(
    "final_status", ["withdrawn", "approved", "changes_requested", "rejected"]
)
def test_request_cards_follow_current_state(
    demo: tuple, db: Session, client: TestClient, final_status: str
) -> None:
    _, people, auth = demo
    principal = principal_for(db, people["E02"].org_id)
    db.add(
        ApprovalPolicy(
            org_id=principal.org_id,
            version=1,
            created_by_user_id=principal.user_id,
            category_overrides={"AI 与数据": False},
        )
    )
    draft = employee.save_draft(principal, db, None, [ItemInput(**payload())], "bot")
    session = store.create_session(db, principal)
    url = f"/api/v1/bot/sessions/{session.id}"
    options = {"params": {"org_id": str(principal.org_id)}, "headers": auth["employee"]}

    def save(card: s.PreviewCard | s.StatusCard) -> None:
        recorded = s.TurnResponse(messages=[card]).model_dump(mode="json")
        if isinstance(card, s.StatusCard):
            for item in recorded["messages"][0]["items"]:
                item.pop("decision", None)
        store.append_message(db, session, "assistant", recorded)
        db.commit()

    def history() -> list[dict]:
        response = client.get(url + "/messages", **options)
        assert response.status_code == 200, response.text
        return [message["payload"]["messages"][0] for message in response.json()]

    preview = tools.preview_card(db, principal, draft.id)
    save(preview)
    assert len(history()[0]["actions"]) == 2
    draft = employee.save_draft(
        principal,
        db,
        draft.id,
        [
            ItemInput(**payload(statement="合成更新")),
            ItemInput(**payload("model_training", statement="合成免审判断")),
        ],
        "bot",
    )
    updated = tools.preview_card(db, principal, draft.id)
    save(updated)
    assert [len(card["actions"]) for card in history()] == [0, 2]
    response = client.post(
        url + "/actions", json=updated.actions[0].model_dump(mode="json"), **options
    )
    assert response.status_code == 200, response.text
    status = s.StatusCard.model_validate(response.json()["messages"][0])
    assert status.status == "pending_review"
    assert [item.decision for item in status.items] == [None, "approved"]
    save(status)
    cards = history()
    assert [len(card["actions"]) for card in cards] == [0, 0, 0, 1]
    assert [item["decision"] for item in cards[-1]["items"]] == [None, "approved"]
    response = client.post(
        url + "/actions", json=updated.actions[1].model_dump(mode="json"), **options
    )
    assert response.status_code == 409
    if final_status == "withdrawn":
        response = client.post(
            url + "/actions", json=status.actions[0].model_dump(mode="json"), **options
        )
        assert response.status_code == 200, response.text
        profile = {
            item.capability.key: item.claim
            for item in employee.my_profile(principal, db).items
        }
        assert profile["model_training"].statement == "合成免审判断"
        assert profile["model_training"].assessment_status == "tentative"
    else:
        row = db.get(ProfileChangeRequest, draft.id)
        assert row is not None
        db.refresh(row)
        row.status = RequestStatus(final_status)
        row.revision += 1
        db.commit()
    cards = history()
    assert all(card["actions"] == [] for card in cards)
    assert all(
        card["status"] == final_status
        for card in cards
        if card["type"] == "status_card"
    )
    response = client.post(
        url + "/actions", json=status.actions[0].model_dump(mode="json"), **options
    )
    assert response.status_code == 409
    saved = store.messages_for(db, session)
    assert saved[0].payload["messages"][0]["actions"]
    assert saved[2].payload["messages"][0]["status"] == "pending_review"
    for headers in (auth["manager"], auth["foreign"]):
        assert (
            client.get(
                url + "/messages", params=options["params"], headers=headers
            ).status_code
            == 404
        )


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
