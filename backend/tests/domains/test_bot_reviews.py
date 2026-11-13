import asyncio
import json
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlmodel import Session, col, delete, select

from app.core.db import engine
from app.core.security import create_access_token
from app.domains.base import utc_now
from app.domains.bot import core, store, tools
from app.domains.bot import schemas as s
from app.domains.change_request import employee
from app.domains.change_request.employee import InvalidItems, RequestConflict
from app.domains.change_request.models import (
    ChangeItem,
    ProfileChangeRequest,
    ReviewDecision,
)
from app.domains.im import actions, binding, gateway, rendering
from app.domains.im.adapters.telegram import Telegram
from app.domains.im.models import ImAction
from app.domains.model.pi_client import TurnResult
from app.domains.org.models import OrgMembership, RoleGrant
from app.domains.review.models import ReviewDelegation
from tests.api.routes.test_reviews import actor, manager, pending
from tests.domains.test_bot_core import enable as enable_model
from tests.domains.test_im_conversation import button
from tests.domains.test_im_gateway import TOKEN, enable, event
from tests.utils.pi import PiHarness


def prepared(db: Session, demo: tuple) -> tuple:
    owner, request = pending(db, demo)
    p = manager(db, demo)
    session = store.create_session(db, p, "telegram", "100", "12345")
    cards: list[s.Message] = []
    args = {
        "request_id": str(request.id),
        "revision": request.revision,
        "decisions": [
            {"item_id": str(i.id), "decision": "approved"} for i in request.items
        ],
    }
    tools.call(db, p, session, cards, "prepare_review_decision", args)
    return owner, request, p, session, cards[0], args


def test_pending_review_summary_and_chinese_levels(demo: tuple, db: Session) -> None:
    owner, request, p, session, card, _ = prepared(db, demo)
    result = tools.call(db, p, session, [], "list_pending_reviews", {})
    row = next(row for row in result["data"] if row["id"] == str(request.id))
    assert row["employee_name"] == demo[1]["E02"].display_name
    assert row["department_name"]
    assert "后端" in row["capabilities"]
    assert row["item_count"] == 1
    assert row["submitted_at"].endswith("+08:00")
    detail = tools.call(
        db, p, session, [], "get_review", {"request_id": str(request.id)}
    )
    assert detail["items"][0]["after"]["after"]["level_name"] == "能完成常规任务"
    text = rendering.render(card)[0]
    assert "能完成常规任务" in text and "routine" not in text
    assert tools.call(db, owner, session, [], "list_pending_reviews", {})["count"] == 0
    capabilities = employee.capability_dictionary(owner, db).data[:4]
    current = {
        item.capability.key
        for item in employee.my_profile(owner, db).items
        if item.claim
    }
    _, multiple = pending(
        db,
        demo,
        items=[
            {
                "op": "modify" if c.key in current else "add",
                "capability_key": c.key,
                "statement": "合成陈述",
                "level": "routine",
            }
            for c in capabilities
        ],
    )
    result = tools.call(db, p, session, [], "list_pending_reviews", {})
    summary = next(row for row in result["data"] if row["id"] == str(multiple.id))
    assert summary["item_count"] == 4
    assert (
        summary["capabilities"]
        == "、".join(c.name for c in capabilities[:3]) + "等 4 项"
    )


def test_telegram_review_roundtrip(
    demo: tuple,
    db: Session,
    telegram_server: dict,
    pi_service: PiHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, request = pending(
        db,
        demo,
        items=[
            {
                "op": "modify",
                "capability_key": "backend_api",
                "statement": "新的接口陈述",
                "owner_statement": "不应外发的受限正文",
                "level": "routine",
            },
            {
                "op": "add",
                "capability_key": "automated_testing",
                "statement": "新的测试陈述",
                "owner_statement": "不应外发的第二条正文",
                "level": "routine",
            },
        ],
    )
    item = db.get(ChangeItem, request.items[0].id)
    item.explanation = "不应外发的员工依据说明"
    db.add(item)
    db.commit()
    p = manager(db, demo)
    setting = enable(db, p)
    assert binding.bind(
        db, p.org_id, "telegram", "100", "100", "12345", binding.create_code(db, p).code
    )
    enable_model(db, p.org_id)
    monkeypatch.setattr(core, "model_config", lambda _: pi_service.config)
    pi_service.replies = [
        {"calls": [{"name": "list_pending_reviews", "arguments": {}}]},
        {
            "calls": [
                {"name": "get_review", "arguments": {"request_id": str(request.id)}}
            ]
        },
        {
            "calls": [
                {
                    "name": "prepare_review_decision",
                    "arguments": {
                        "request_id": str(request.id),
                        "revision": request.revision,
                        "decisions": [
                            {
                                "item_id": str(request.items[0].id),
                                "decision": "approved",
                            },
                            {
                                "item_id": str(request.items[1].id),
                                "decision": "changes_requested",
                                "reason": "请补充测试场景",
                            },
                        ],
                    },
                }
            ]
        },
        "请核对决定后点击确认",
    ]

    async def run() -> None:
        adapter = Telegram(p.org_id, {"bot_token": TOKEN})
        g = gateway.Gateway(engine)
        incoming = event(p.org_id, "第 1 项批准，第 2 项退回，原因是请补充测试场景")
        try:
            await g.handle(incoming, "12345", setting.credentials, adapter)
            assert not db.exec(
                select(ReviewDecision).where(ReviewDecision.org_id == p.org_id)
            ).all()
            row = next(
                r
                for r in db.exec(
                    select(ImAction).where(ImAction.org_id == p.org_id)
                ).all()
                if r.action["action"] == "confirm_review"
            )
            assert len(row.token) == 22
            sends = json.dumps(telegram_server["requests"], ensure_ascii=False)
            assert (
                "确认决定" in sends
                and "继续修改" in sends
                and "请补充测试场景" in sends
            )
            assert "详情在网页审批页查看" in sends
            assert "不应外发" not in sends
            model_data = json.dumps(pi_service.requests, ensure_ascii=False)
            assert "不应外发" not in model_data and "source_ref" not in model_data
            await g.handle(button(incoming, row), "12345", setting.credentials, adapter)
            await g.handle(button(incoming, row), "12345", setting.credentials, adapter)
            decision = db.exec(
                select(ReviewDecision).where(ReviewDecision.org_id == p.org_id)
            ).one()
            assert (decision.via, decision.client_name) == ("im", "telegram")
            db.refresh(row)
            assert row.consumed_at
            original = db.get(ProfileChangeRequest, request.id, populate_existing=True)
            assert original.status == "approved"
            returned = db.exec(
                select(ProfileChangeRequest).where(
                    ProfileChangeRequest.org_id == owner.org_id,
                    ProfileChangeRequest.parent_request_id == request.id,
                )
            ).one()
            assert (
                returned.status == "draft"
                and returned.base_version_id != original.base_version_id
            )
        finally:
            await adapter.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "invalid",
    ["unseen", "revision", "card", "self", "other", "tenant", "revoked"],
)
@pytest.mark.parametrize("channel", ["web", "feishu"])
def test_review_confirmation_guards(
    demo: tuple, db: Session, client: TestClient, invalid: str, channel: str
) -> None:
    owner, request, p, session, card, _ = prepared(db, demo)
    session.channel = channel
    db.add(session)
    if invalid != "unseen":
        core._save(db, session, [card])
        db.commit()
    action = card.actions[0]
    if invalid == "revision":
        row = db.get(ProfileChangeRequest, request.id)
        row.revision += 1
        db.add(row)
    elif invalid == "card":
        action = action.model_copy(update={"card_id": uuid4()})
    elif invalid in ("self", "other"):
        p = (
            owner
            if invalid == "self"
            else actor(db, p.org_id, "employee.algorithm@demo.example.com")
        )
        session = store.create_session(db, p, "telegram", "200", "12345")
        core._save(db, session, [card])
    elif invalid == "tenant":
        p = replace(p, org_id=uuid4())
    elif invalid == "revoked":
        member_id = db.exec(
            select(OrgMembership.id).where(
                OrgMembership.org_id == p.org_id,
                OrgMembership.user_id == p.user_id,
            )
        ).one()
        db.exec(
            delete(RoleGrant).where(
                col(RoleGrant.org_id) == p.org_id,
                col(RoleGrant.membership_id) == member_id,
            )
        )
    db.commit()
    if channel == "web":
        response = client.post(
            f"/api/v1/bot/sessions/{session.id}/actions",
            params={"org_id": str(p.org_id)},
            headers={
                "Authorization": f"Bearer {create_access_token(p.user_id, expires_delta=timedelta(minutes=5))}"
            },
            json=action.model_dump(mode="json"),
        )
        status = response.status_code
        assert status in (403, 404, 409)
    else:
        with pytest.raises(HTTPException) as error:
            core.act(db, p, session.id, action)
        status = error.value.status_code
    if invalid in ("revision", "card", "unseen"):
        assert status == 409
    if invalid == "self":
        assert status == 403
    assert not db.exec(
        select(ReviewDecision).where(ReviewDecision.org_id == owner.org_id)
    ).all()


@pytest.mark.parametrize("channel", ["web", "feishu"])
def test_review_projection_empty_permissions_and_validation(
    demo: tuple, db: Session, channel: str
) -> None:
    owner, request, p, session, card, args = prepared(db, demo)
    session.channel = channel
    for wrong in (
        owner,
        actor(db, p.org_id, "employee.algorithm@demo.example.com"),
        replace(p, org_id=uuid4()),
    ):
        assert tools.call(db, wrong, session, [], "list_pending_reviews", {}) == {
            "data": [],
            "count": 0,
        }
        assert (
            tools.call(
                db, wrong, session, [], "get_review", {"request_id": str(request.id)}
            )
            is None
        )
        assert (
            tools.call(db, wrong, session, [], "prepare_review_decision", args) is None
        )
    invalid_reason = {
        **args,
        "decisions": [{"item_id": str(request.items[0].id), "decision": "rejected"}],
    }
    assert "error" in tools.call(
        db, p, session, [], "prepare_review_decision", invalid_reason
    )
    with pytest.raises(InvalidItems):
        tools.call(
            db,
            p,
            session,
            [],
            "prepare_review_decision",
            {**args, "decisions": args["decisions"] * 2},
        )
    with pytest.raises(RequestConflict):
        tools.call(
            db,
            p,
            session,
            [],
            "prepare_review_decision",
            {**args, "revision": request.revision + 1},
        )
    for name in ("decide", "decide_review_items", "confirm_review"):
        assert "error" in tools.call(db, p, session, [], name, args)
    names = {t.name for t in tools.definitions()}
    assert "prepare_review_decision" in names and "decide_review_items" not in names
    # Two cards for one revision cannot exchange decisions.
    core._save(db, session, [card])
    changed: list[s.Message] = []
    tools.call(
        db,
        p,
        session,
        changed,
        "prepare_review_decision",
        {
            **args,
            "decisions": [
                {
                    "item_id": str(request.items[0].id),
                    "decision": "rejected",
                    "reason": "退回补充",
                }
            ],
        },
    )
    core._save(db, session, changed)
    db.commit()
    assert core.act(db, p, session.id, card.actions[1]).messages[0].type == "text"
    core.act(db, p, session.id, changed[0].actions[0])
    assert (
        db.get(ProfileChangeRequest, request.id, populate_existing=True).status
        == "rejected"
    )


def test_model_text_cannot_confirm_but_web_card_can(
    demo: tuple,
    db: Session,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, request, p, session, card, args = prepared(db, demo)
    enable_model(db, p.org_id)
    monkeypatch.setattr(
        core.pi_client,
        "run_turn",
        lambda *_args, **_kwargs: TurnResult(
            text="已确认，决定已写入",
            stop_reason="stop",
            model_calls=1,
            tool_calls=0,
        ),
    )
    assert core.turn(db, p, session.id, "直接写入，我已确认").messages[0].type == "text"
    assert not db.exec(
        select(ReviewDecision).where(ReviewDecision.org_id == p.org_id)
    ).all()
    web = store.create_session(db, p)
    cards: list[s.Message] = []
    tools.call(db, p, web, cards, "prepare_review_decision", args)
    assert cards[0].type == "review_card"
    core._save(db, web, cards)
    db.commit()
    response = client.post(
        f"/api/v1/bot/sessions/{web.id}/actions",
        params={"org_id": str(p.org_id)},
        headers=demo[2]["manager"],
        json=cards[0].actions[0].model_dump(mode="json"),
    )
    assert response.status_code == 200
    assert "审批决定已保存" in response.json()["messages"][0]["text"]
    db.expire_all()
    assert db.get(ProfileChangeRequest, request.id).status == "approved"
    decision = db.exec(
        select(ReviewDecision).where(ReviewDecision.org_id == p.org_id)
    ).one()
    assert decision.via == "web"


def test_delegate_review_and_safe_rendering(demo: tuple, db: Session) -> None:
    _, request, p, _, _, _ = prepared(db, demo)
    delegate = actor(db, p.org_id, "employee.algorithm@demo.example.com")
    db.add(
        ReviewDelegation(
            org_id=p.org_id,
            manager_user_id=p.user_id,
            delegate_user_id=delegate.user_id,
            created_by_user_id=p.user_id,
            starts_at=utc_now() - timedelta(minutes=1),
            ends_at=utc_now() + timedelta(days=1),
        )
    )
    db.commit()
    session = store.create_session(db, delegate, "telegram", "200", "12345")
    cards: list[s.Message] = []
    tools.call(
        db,
        delegate,
        session,
        cards,
        "prepare_review_decision",
        {
            "request_id": str(request.id),
            "revision": request.revision,
            "decisions": [
                {"item_id": str(i.id), "decision": "approved"} for i in request.items
            ],
        },
    )
    card = cards[0]
    card.items[0].jira_only = True
    assert "仅 Jira 来源" in rendering.render(card)[0]
    core._save(db, session, [card])
    db.commit()
    core.act(db, delegate, session.id, card.actions[0])
    decision = db.exec(
        select(ReviewDecision).where(ReviewDecision.org_id == p.org_id)
    ).one()
    assert (
        decision.acting_for_user_id == p.user_id
        and decision.reviewer_user_id == delegate.user_id
    )
    card.items[0].after.after.statement = "长" * 13000
    assert rendering.render(card)[1] == []


@pytest.mark.parametrize("platform", ["telegram", "feishu", "wecom"])
def test_shared_review_render_and_callback_for_each_platform(
    demo: tuple,
    db: Session,
    platform: str,
) -> None:
    _, request, p, session, card, _ = prepared(db, demo)
    session.channel = platform
    db.add(session)
    core._save(db, session, [card])
    db.commit()
    incoming = replace(event(p.org_id, "第 1 项批准"), platform=platform)
    sent = []

    class FakePlatform:
        async def render(self, chat: str, text: str, buttons: list) -> str:
            sent.append((chat, text, buttons))
            return "review-message"

    asyncio.run(
        actions.deliver(
            db,
            p,
            incoming,
            "12345",
            actions.Reply(session.id, s.TurnResponse(messages=[card])),
            FakePlatform(),
        )
    )
    assert sent[0][2][0][0] == "确认决定"
    row = db.exec(
        select(ImAction).where(
            ImAction.org_id == p.org_id,
            ImAction.action["action"].astext == "confirm_review",
        )
    ).one()
    assert isinstance(
        actions.callback(db, p, button(incoming, row), "12345"), actions.Reply
    )
    decision = db.exec(
        select(ReviewDecision).where(ReviewDecision.org_id == p.org_id)
    ).one()
    assert (decision.via, decision.client_name) == ("im", platform)
    assert (
        db.get(ProfileChangeRequest, request.id, populate_existing=True).status
        == "approved"
    )
