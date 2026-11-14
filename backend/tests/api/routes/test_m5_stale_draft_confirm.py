import json
from datetime import timedelta
from typing import Literal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.domains.audit.models import AuditEvent
from app.domains.base import utc_now
from app.domains.bot import core, store, tools
from app.domains.bot import schemas as s
from app.domains.change_request import employee
from app.domains.evidence.models import Evidence
from app.domains.im import actions
from app.domains.im.adapters.base import Event
from app.domains.im.models import ImAction
from app.domains.inbox.models import InboxItem
from tests.api.routes.test_employee_requests import principal_for
from tests.api.routes.test_m47_stale_initial_draft import (
    assert_formal_unchanged,
    initial_draft,
    publish,
)
from tests.api.routes.test_mcp import call, initialize, token_for


def test_unpreviewed_initial_stays_pending(
    demo: tuple, client: TestClient, db: Session
) -> None:
    base, initial = initial_draft(client, demo)
    formal = publish(client, demo)
    before = side_effects(db, demo[1]["E02"].org_id)
    response = client.post(
        f"{base}/submit",
        headers=demo[2]["employee"],
        json={"revision": initial["revision"]},
    )
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "stale_initial_draft"
    assert detail["capabilities"][0]["key"] == "automated_testing"
    assert "请先打开预览核对，再确认提交" in detail["message"]
    requests = client.get(f"{demo[0]}/me/requests", headers=demo[2]["employee"]).json()
    assert next(r for r in requests["data"] if r["id"] == initial["id"]) == initial
    assert side_effects(db, demo[1]["E02"].org_id) == before
    assert_formal_unchanged(client, demo, formal)


def side_effects(db: Session, org_id: UUID) -> tuple:
    return tuple(
        set(db.exec(select(model.id).where(model.org_id == org_id)).all())
        for model in (Evidence, AuditEvent, InboxItem)
    )


def test_confirmed_edit_and_stale_preview(demo: tuple, client: TestClient) -> None:
    base, _ = initial_draft(client, demo)
    publish(client, demo)
    auth = demo[2]["employee"]
    preview = client.get(f"{base}/preview", headers=auth).json()
    formal = publish(client, demo, statement="经理再次修改")
    stale = client.post(
        f"{base}/submit",
        headers=auth,
        json={
            "revision": preview["revision"],
            "base_revision": preview["base_revision"],
        },
    )
    assert stale.status_code == 409
    saved = client.put(
        f"{base}/items",
        headers=auth,
        json={
            "items": [
                {
                    "op": "modify",
                    "capability_key": "automated_testing",
                    "statement": "员工核对后的陈述",
                    "level": "routine",
                    "owner_statement": "员工确认的依据",
                }
            ]
        },
    ).json()
    latest = client.get(f"{base}/preview", headers=auth).json()
    assert latest["base_revision"] > preview["base_revision"]
    assert latest["items"][0]["before"]["statement"] == "经理再次修改"
    result = client.post(
        f"{base}/submit",
        headers=auth,
        json={
            "revision": saved["revision"],
            "base_revision": latest["base_revision"],
        },
    )
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "pending_review"
    assert result.json()["items"][0]["after"]["statement"] == "员工核对后的陈述"
    assert_formal_unchanged(client, demo, formal)


def test_mcp_requires_confirmation(
    demo: tuple, client: TestClient, db: Session
) -> None:
    _, initial = initial_draft(client, demo)
    publish(client, demo)
    headers = initialize(client, token_for(db, demo[1]["E02"])[0])
    args = {"request_id": initial["id"], "revision": initial["revision"]}
    before = employee.my_requests(principal_for(db, demo[1]["E02"].org_id), db, "all")
    result = call(client, headers, "submit_request", args)
    assert result["isError"]
    detail = json.loads(result["content"][0]["text"])
    assert detail["status"] == 409
    assert detail["detail"]["capabilities"][0]["key"] == "automated_testing"
    assert "请先打开预览核对，再确认提交" in detail["detail"]["message"]
    assert (
        employee.my_requests(principal_for(db, demo[1]["E02"].org_id), db, "all")
        == before
    )
    preview = call(client, headers, "preview_request", {"request_id": initial["id"]})[
        "structuredContent"
    ]
    result = call(
        client,
        headers,
        "submit_request",
        {**args, "base_revision": preview["base_revision"]},
    )
    assert not result.get("isError")
    assert result["structuredContent"]["status"] == "pending_review"


@pytest.mark.parametrize("channel", ["web", "feishu"])
def test_assistant_old_card_requires_new_click(
    demo: tuple, client: TestClient, db: Session, channel: Literal["web", "feishu"]
) -> None:
    _, initial = initial_draft(client, demo)
    publish(client, demo)
    p = principal_for(db, demo[1]["E02"].org_id)
    session = store.create_session(db, p, channel)
    cards: list[s.Message] = []
    tools.call(db, p, session, cards, "preview_request", {"request_id": initial["id"]})
    card = next(c for c in cards if isinstance(c, s.PreviewCard))
    assert card.actions[0].base_revision == card.base_revision
    core._save(db, session, cards)
    db.commit()
    formal = publish(client, demo, statement="经理在卡片显示后修改")
    before = employee.my_requests(p, db, "all")
    if channel == "web":
        response = core.act(db, p, session.id, card.actions[0])
    else:
        session.bot_identity = "synthetic-app"
        session.external_chat_id = "synthetic-chat"
        row = ImAction(
            token="synthetic-stale",
            org_id=p.org_id,
            platform=channel,
            bot_identity=session.bot_identity,
            employee_id=p.employee_id,
            session_id=session.id,
            external_chat_id=session.external_chat_id,
            message_id="synthetic-message",
            action=card.actions[0].model_dump(mode="json"),
            expires_at=utc_now() + timedelta(hours=1),
        )
        db.add(row)
        db.commit()
        reply = actions.callback(
            db,
            p,
            Event(
                platform=channel,
                org_id=p.org_id,
                event_id="synthetic-event",
                external_user_id="synthetic-user",
                external_chat_id="synthetic-chat",
                message_id="synthetic-message",
                token=row.token,
            ),
            "synthetic-app",
        )
        assert isinstance(reply, actions.Reply)
        response = reply.response
    assert employee.my_requests(p, db, "all") == before
    assert_formal_unchanged(client, demo, formal)
    assert any(
        isinstance(m, s.TextMessage) and "请先打开预览核对，再确认提交" in m.text
        for m in response.messages
    )
    latest = next(m for m in response.messages if isinstance(m, s.PreviewCard))
    assert latest.base_revision > card.base_revision
    assert latest.items[0].before.statement == "经理在卡片显示后修改"
    core.act(db, p, session.id, latest.actions[0])
    assert (
        next(
            r
            for r in employee.my_requests(p, db, "all").data
            if str(r.id) == initial["id"]
        ).status
        == "pending_review"
    )
