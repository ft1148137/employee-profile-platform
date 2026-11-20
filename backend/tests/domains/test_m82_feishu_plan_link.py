import asyncio
import json
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlmodel import Session, select

from app.core.config import settings
from app.domains.bot import delivery, store
from app.domains.bot import schemas as s
from app.domains.im import actions, rendering
from app.domains.im.adapters.base import Event
from app.domains.im.adapters.feishu import Feishu
from app.domains.im.models import ImAction
from app.domains.im.schemas import Platform
from tests.domains.test_im_gateway import principal


@pytest.mark.parametrize(
    "host", ["https://profiles.example", "https://profiles.example/"]
)
@pytest.mark.parametrize("scope_changed", [False, True])
def test_plan_link_through_delivery(
    db: Session,
    demo: tuple,
    monkeypatch: pytest.MonkeyPatch,
    host: str,
    scope_changed: bool,
) -> None:
    monkeypatch.setattr(settings, "FRONTEND_HOST", host)
    person = demo[1]["E02"]
    p = principal(db, person.org_id, person.id)
    session = store.create_session(db, p, channel="feishu")
    card = s.PlanCard(
        title="[合成方案](https://untrusted.example)",
        fields=[s.CardField(label="结论", value="按期交付")],
        link=f"/capacity?tab=incoming&demand={uuid4()}&plans=1",
    )
    response = s.TurnResponse(messages=[card])
    response._principal = p
    response._scope = store.session_scope(db, p)
    if scope_changed:
        # A previously visible employee is no longer in the current scope.
        response._scope["visible_employee_ids"].append(str(uuid4()))
    db.rollback()
    request = AsyncMock(return_value={"data": {"message_id": "synthetic-message"}})
    monkeypatch.setattr(Feishu, "_request", request)

    async def send() -> None:
        adapter = Feishu(p.org_id, {})
        try:
            await actions.deliver(
                db,
                p,
                Event(p.org_id, "feishu", "event", external_chat_id="chat"),
                "synthetic-app",
                actions.Reply(session.id, response),
                adapter,
            )
        finally:
            await adapter.close()

    asyncio.run(send())
    request.assert_awaited_once()
    method, path, body = request.call_args.args
    assert method == "POST" and path == "im/v1/messages?receive_id_type=chat_id"
    assert body["receive_id"] == "chat" and body["msg_type"] == "interactive"
    elements = json.loads(body["content"])["elements"]
    if scope_changed:
        assert elements == [
            {
                "tag": "div",
                "text": {"tag": "plain_text", "content": delivery.SCOPE_CHANGED},
            }
        ]
        assert card.link not in body["content"]
    else:
        assert elements[0] == {
            "tag": "div",
            "text": {"tag": "plain_text", "content": rendering.render(card)[0]},
        }
        assert elements[1] == {
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "在方案页修改"},
                    "url": f"https://profiles.example{card.link}",
                }
            ],
        }
    assert not db.exec(
        select(ImAction).where(
            ImAction.org_id == p.org_id, ImAction.session_id == session.id
        )
    ).all()


@pytest.mark.parametrize("platform", ["telegram", "wecom", "feishu"])
def test_three_argument_renderer_compatibility(
    db: Session, demo: tuple, platform: Platform
) -> None:
    person = demo[1]["E02"]
    p = principal(db, person.org_id, person.id)
    session = store.create_session(db, p, channel=platform)
    message = (
        s.TextMessage(text="普通消息中的 https://untrusted.example 不生成按钮")
        if platform == "feishu"
        else s.PlanCard(title="合成方案", fields=[], link="/capacity?plans=1")
    )
    sent = []

    class FakePlatform:
        async def render(self, chat: str, text: str, buttons: list) -> str:
            sent.append((chat, text, buttons))
            return "synthetic-message"

    asyncio.run(
        actions.deliver(
            db,
            p,
            Event(p.org_id, platform, "event", external_chat_id="chat"),
            "synthetic-app",
            actions.Reply(session.id, s.TurnResponse(messages=[message])),
            FakePlatform(),
        )
    )
    assert sent == [("chat", rendering.render(message)[0], [])]


@pytest.mark.parametrize(
    "text",
    ["确认卡", "长" * 3001, "\x01" * 2900],
    ids=["callbacks", "long-text", "large-json"],
)
def test_callback_and_size_limits(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    request = AsyncMock(return_value={"data": {"message_id": "synthetic-message"}})
    monkeypatch.setattr(Feishu, "_request", request)
    buttons = [
        ("确认", "confirm"),
        ("取消", "cancel"),
        ("继续", "edit"),
        ("多余", "extra"),
    ]

    async def send() -> None:
        adapter = Feishu(uuid4(), {})
        try:
            await adapter.render(
                "chat",
                text,
                buttons,
                links=[("在方案页修改", "https://profiles.example/capacity")],
            )
        finally:
            await adapter.close()

    asyncio.run(send())
    content = request.call_args.args[2]["content"]
    elements = json.loads(content)["elements"]
    assert len(content.encode()) <= 10 * 1024
    if text == "确认卡":
        assert elements[1]["actions"] == [
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": label},
                "value": {"token": token},
            }
            for label, token in buttons[:3]
        ]
    else:
        assert len(elements) == 1
        assert (
            elements[0]["text"]["content"] == "内容较长，请在网页查看完整内容并操作。"
        )


def test_feishu_fallback_plan_and_capacity_cards_keep_both_clickable_links(
    db: Session,
    demo: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "FRONTEND_HOST", "https://profiles.example")
    person = demo[1]["E02"]
    p = principal(db, person.org_id, person.id)
    session = store.create_session(db, p, channel="feishu")
    demand_id = uuid4()
    case_id = uuid4()
    custom_id = uuid4()
    rule_id = uuid4()
    plan_card = s.PlanCard(
        title="自定方案方案",
        fields=[s.CardField(label="结论", value="不可行")],
        link=f"/capacity?tab=incoming&demand={demand_id}&plans=1#case={case_id}&option={custom_id}",
        rule_fallback=s.RulePlanFallback(
            fields=[s.CardField(label="结论", value="可行")],
            differences=[s.CardField(label="结论", value="不可行 → 可行")],
            link=f"/capacity?tab=incoming&demand={demand_id}&plans=1#case={case_id}&option={rule_id}",
        ),
    )
    capacity_card = s.CapacityCard(
        kind="demand_reply",
        title="答复部门需求",
        content="合成答复内容",
        arguments={},
        actions=[
            s.CapacityAction(action="confirm_capacity", card_id=uuid4()),
            s.CapacityAction(action="cancel_capacity", card_id=uuid4()),
        ],
        plan=plan_card,
    )
    request = AsyncMock(return_value={"data": {"message_id": "synthetic-message"}})
    monkeypatch.setattr(Feishu, "_request", request)

    async def send() -> None:
        adapter = Feishu(p.org_id, {})
        try:
            await actions.deliver(
                db,
                p,
                Event(p.org_id, "feishu", "event", external_chat_id="chat"),
                "synthetic-app",
                actions.Reply(
                    session.id, s.TurnResponse(messages=[plan_card, capacity_card])
                ),
                adapter,
            )
        finally:
            await adapter.close()

    asyncio.run(send())
    assert request.await_count == 2
    first_elements = json.loads(request.call_args_list[0].args[2]["content"])[
        "elements"
    ]
    assert first_elements[1] == {
        "tag": "action",
        "actions": [
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "在方案页修改"},
                "url": f"https://profiles.example{plan_card.link}",
            },
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "查看备选方案"},
                "url": f"https://profiles.example{plan_card.rule_fallback.link}",
            },
        ],
    }
    second_elements = json.loads(request.call_args_list[1].args[2]["content"])[
        "elements"
    ]
    assert [btn["text"]["content"] for btn in second_elements[1]["actions"]] == [
        "确认",
        "取消",
    ]
    assert second_elements[2] == first_elements[1]
    assert (
        second_elements[2]["actions"][0]["url"]
        != second_elements[2]["actions"][1]["url"]
    )
