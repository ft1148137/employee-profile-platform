import asyncio
import json
from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import Engine, inspect, text
from sqlmodel import Session, select

from app.core.db import engine
from app.domains.base import utc_now
from app.domains.bot import core, store, tools
from app.domains.bot import schemas as s
from app.domains.bot.models import BotMessage
from app.domains.change_request import employee
from app.domains.change_request.models import ProfileChangeRequest
from app.domains.change_request.schemas import ItemInput
from app.domains.im import actions, binding, gateway, rendering
from app.domains.im.adapters.base import Attachment, Event
from app.domains.im.adapters.telegram import Telegram
from app.domains.im.models import ImAction
from app.domains.inbox.models import PendingQuestion
from app.domains.inbox.service import answer_question
from tests.api.routes.test_employee_requests import payload
from tests.domains.test_bot_core import enable as enable_model
from tests.domains.test_im_gateway import TOKEN, enable, event, principal
from tests.utils.migrations import migrate
from tests.utils.pi import PiHarness


def setup(db: Session, demo: tuple) -> tuple:
    p = principal(db, demo[1]["E02"].org_id, demo[1]["E02"].id)
    setting = enable(db, p)
    assert binding.bind(
        db, p.org_id, "telegram", "100", "100", "12345", binding.create_code(db, p).code
    )
    return p, setting


def preview(db: Session, demo: tuple) -> tuple:
    p, setting = setup(db, demo)
    incoming = event(p.org_id, "合成经历")
    session = actions.conversation(db, p, incoming, "12345")
    request = employee.save_draft(
        p, db, None, [ItemInput(**payload(owner_statement="本人合成经历"))], "bot"
    )
    response = core._save(db, session, [tools.preview_card(db, p, request.id)])
    db.commit()
    return p, setting, incoming, session, request, response


def button(incoming: Event, row: ImAction) -> Event:
    return replace(
        incoming,
        event_id=uuid4().hex,
        text=None,
        callback_id=uuid4().hex,
        token=row.token,
        message_id=row.message_id,
    )


def test_feishu_one_preview_and_submitted_state(
    demo: tuple, db: Session, pi_service: PiHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = principal(db, demo[1]["E02"].org_id, demo[1]["E02"].id)
    enable_model(db, p.org_id)
    monkeypatch.setattr(core, "model_config", lambda _: pi_service.config)
    draft = employee.save_draft(p, db, None, [ItemInput(**payload())], "bot")
    pi_service.replies = [
        {
            "calls": [
                {
                    "name": "draft_update_request",
                    "arguments": {
                        "request_id": str(draft.id),
                        "items": [
                            payload(statement="飞书唯一预览陈述", level="routine")
                        ],
                    },
                }
            ]
        },
        {
            "calls": [
                {"name": "preview_request", "arguments": {"request_id": str(draft.id)}}
            ]
        },
        {"calls": [{"name": "get_my_requests", "arguments": {}}]},
        "请核对卡片",
    ]
    incoming = replace(event(p.org_id, "补充经历"), platform="feishu")
    reply = actions.text(db, p, incoming, "synthetic-app")
    cards = [
        m
        for m in reply.response.messages
        if isinstance(m, s.PreviewCard) and m.request_id == draft.id
    ]
    assert len(cards) == 1
    sent: list[str] = []

    class FakePlatform:
        async def render(self, chat: str, text: str, buttons: list) -> str:
            sent.append(text)
            return f"message-{len(sent)}"

    asyncio.run(
        actions.deliver(db, p, incoming, "synthetic-app", reply, FakePlatform())
    )
    assert sum("飞书唯一预览陈述" in text for text in sent) == 1
    rendered = next(text for text in sent if "飞书唯一预览陈述" in text)
    assert "能完成常规任务" in rendered and "routine" not in rendered
    row = next(
        r
        for r in db.exec(select(ImAction).where(ImAction.org_id == p.org_id)).all()
        if r.action.get("request_id") == str(draft.id)
        and r.action["action"] == "confirm_submit"
    )
    submitted = actions.callback(db, p, button(incoming, row), "synthetic-app")
    assert isinstance(submitted, actions.Reply)
    status = submitted.response.messages[0]
    assert isinstance(status, s.StatusCard) and status.status == "pending_review"
    assert "能完成常规任务" in rendering.render(status)[0]
    pi_service.replies = ["已经提交，待经理审批"]
    actions.text(db, p, replace(incoming, text="提交了吗？"), "synthetic-app")
    prompt = json.dumps(
        pi_service.requests[-1]["body"]["messages"][0], ensure_ascii=False
    )
    assert str(draft.id) in prompt and "待经理审批" in prompt
    assert "飞书唯一预览陈述" not in prompt


def test_telegram_turn_submit_withdraw(
    demo: tuple,
    db: Session,
    telegram_server: dict,
    pi_service: PiHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    p, setting = setup(db, demo)
    enable_model(db, p.org_id)
    monkeypatch.setattr(core, "model_config", lambda _: pi_service.config)
    pi_service.replies = [
        {
            "calls": [
                {
                    "name": "draft_update_request",
                    "arguments": {"items": [payload(owner_statement="本人合成经历")]},
                }
            ]
        },
        "请确认预览",
    ]

    async def run() -> None:
        adapter = Telegram(p.org_id, {"bot_token": TOKEN})
        g = gateway.Gateway(engine)

        async def pump(update: dict) -> None:
            telegram_server["updates"] = [update]
            async for incoming in adapter.poll():
                await g.handle(incoming, "12345", setting.credentials, adapter)

        def callback(update_id: int, token: str, message_id: str) -> dict:
            return {
                "update_id": update_id,
                "callback_query": {
                    "id": str(update_id),
                    "from": {"id": 100, "is_bot": False},
                    "data": token,
                    "message": {
                        "message_id": int(message_id),
                        "chat": {"id": 100, "type": "private"},
                    },
                },
            }

        try:
            await pump(
                {
                    "update_id": 1,
                    "message": {
                        "from": {"id": 100, "is_bot": False},
                        "chat": {"id": 100, "type": "private"},
                        "text": "我补充本人合成经历",
                    },
                }
            )
            row = db.exec(select(ImAction).where(ImAction.org_id == p.org_id)).all()[0]
            request_id = UUID(str(row.action["request_id"]))
            assert row.action["action"] == "confirm_submit"
            assert len(row.token.encode()) == 22
            assert db.get(ProfileChangeRequest, request_id).status == "draft"
            before = len(telegram_server["requests"])
            await pump(callback(2, row.token, row.message_id))
            assert telegram_server["requests"][before][0] == "getUpdates"
            assert telegram_server["requests"][before + 1][0] == "answerCallbackQuery"
            assert any(
                m == "editMessageReplyMarkup" for m, _ in telegram_server["requests"]
            )
            request = db.get(ProfileChangeRequest, request_id, populate_existing=True)
            assert (request.status, request.submitted_via, request.client_name) == (
                "pending_review",
                "bot",
                "内置机器人（Telegram）",
            )
            db.refresh(row)
            assert row.consumed_at
            # A new gateway models process restart; event and token idempotency persist.
            g = gateway.Gateway(engine)
            await pump(callback(2, row.token, row.message_id))
            await pump(callback(3, row.token, row.message_id))
            assert telegram_server["requests"][-1][1]["text"] == "已处理"
            withdraw = next(
                r
                for r in db.exec(
                    select(ImAction).where(ImAction.org_id == p.org_id)
                ).all()
                if r.action["action"] == "withdraw"
            )
            await pump(callback(4, withdraw.token, withdraw.message_id))
            db.refresh(request)
            assert request.status == "withdrawn"
            messages = db.exec(
                select(BotMessage).where(BotMessage.org_id == p.org_id)
            ).all()
            assert messages and all("合成经历" not in m.ciphertext for m in messages)
        finally:
            await adapter.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "invalid",
    [
        "token",
        "employee",
        "chat",
        "message",
        "group",
        "tenant",
        "bot",
        "expired",
        "inline",
        "revision",
        "unseen",
        "idle",
        "busy",
        "disabled",
        "unbound",
    ],
)
def test_action_scope_and_staleness(
    demo: tuple, db: Session, telegram_server: dict, invalid: str
) -> None:
    p, setting, incoming, session, request, response = preview(db, demo)

    async def send() -> None:
        adapter = Telegram(p.org_id, {"bot_token": TOKEN})
        try:
            await actions.deliver(
                db, p, incoming, "12345", actions.Reply(session.id, response), adapter
            )
        finally:
            await adapter.close()

    asyncio.run(send())
    assert telegram_server["requests"]
    row = db.exec(select(ImAction).where(ImAction.org_id == p.org_id)).first()
    click = button(incoming, row)
    identity = "12345"
    if invalid == "token":
        click = replace(click, token="unknown-token")
    elif invalid == "employee":
        other = principal(db, p.org_id, demo[1]["E01"].id)
        assert binding.bind(
            db,
            p.org_id,
            "telegram",
            "200",
            "200",
            identity,
            binding.create_code(db, other).code,
        )
        click = replace(click, external_user_id="200", external_chat_id="200")
    elif invalid == "chat":
        click = replace(click, external_chat_id="999")
    elif invalid == "message":
        click = replace(click, message_id="999")
    elif invalid == "group":
        click = replace(click, private=False)
    elif invalid == "tenant":
        assert isinstance(
            actions.callback(db, replace(p, org_id=uuid4()), click, identity), str
        )
        return
    elif invalid == "bot":
        identity = "999"
    elif invalid == "expired":
        row.expires_at = utc_now() - timedelta(seconds=1)
        db.add(row)
    elif invalid == "inline":
        click = replace(click, message_id=None, external_chat_id="", private=False)
    elif invalid == "revision":
        record = db.get(ProfileChangeRequest, request.id)
        record.revision += 1
        db.add(record)
    elif invalid == "unseen":
        from sqlmodel import delete

        db.exec(delete(BotMessage).where(BotMessage.org_id == p.org_id))
    elif invalid == "idle":
        session.last_activity_at = utc_now() - timedelta(hours=3)
        db.add(session)
    elif invalid == "busy":
        session.lease_until = utc_now() + timedelta(minutes=1)
        db.add(session)
    elif invalid == "disabled":
        setting.enabled = False
        db.add(setting)
    elif invalid == "unbound":
        binding.unbind(db, p, "telegram")
    db.commit()
    gateway.Gateway(engine).dispatch(click, identity, setting.credentials)
    db.refresh(row)
    assert row.consumed_at is None
    assert (
        db.get(ProfileChangeRequest, request.id, populate_existing=True).status
        == "draft"
    )


def test_action_failure_rolls_back_submission(
    demo: tuple, db: Session, telegram_server: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    p, setting, incoming, session, request, response = preview(db, demo)

    async def run() -> None:
        adapter = Telegram(p.org_id, {"bot_token": TOKEN})
        try:
            await actions.deliver(
                db, p, incoming, "12345", actions.Reply(session.id, response), adapter
            )
        finally:
            await adapter.close()

    asyncio.run(run())
    assert telegram_server["requests"]
    row = db.exec(select(ImAction).where(ImAction.org_id == p.org_id)).first()

    def fail(*_args: object) -> None:
        raise HTTPException(409, "合成事务失败")

    monkeypatch.setattr(core, "_save", fail)
    gateway.Gateway(engine).dispatch(
        button(incoming, row), "12345", setting.credentials
    )
    db.refresh(row)
    assert row.consumed_at is None
    assert (
        db.get(ProfileChangeRequest, request.id, populate_existing=True).status
        == "draft"
    )


def test_question_next_text_and_channel_idle_isolation(
    demo: tuple, db: Session, telegram_server: dict
) -> None:
    p, setting = setup(db, demo)
    incoming = event(p.org_id, "经历")
    session = actions.conversation(db, p, incoming, "12345")
    web = store.create_session(db, p)
    question = PendingQuestion(
        org_id=p.org_id,
        target_employee_id=p.employee_id,
        question="还在使用这项能力吗？",
        reason="定期确认",
    )
    db.add(question)
    db.commit()
    response = core._save(
        db, session, [tools.question_card(s.Question.model_validate(question))]
    )
    db.commit()

    async def run() -> None:
        adapter = Telegram(p.org_id, {"bot_token": TOKEN})
        try:
            await actions.deliver(
                db, p, incoming, "12345", actions.Reply(session.id, response), adapter
            )
            row = db.exec(select(ImAction).where(ImAction.org_id == p.org_id)).one()
            g = gateway.Gateway(engine)
            await g.handle(button(incoming, row), "12345", setting.credentials, adapter)
            db.refresh(session)
            assert session.pending_question_id == question.id
            await g.handle(
                replace(incoming, event_id=uuid4().hex, text="我仍用它设计合成测试"),
                "12345",
                setting.credentials,
                adapter,
            )
            db.refresh(question)
            db.refresh(session)
            assert question.status == "answered" and session.pending_question_id is None
            assert (
                actions.conversation(db, p, incoming, "12345").id
                == session.id
                != web.id
            )
            session.last_activity_at = utc_now() - timedelta(hours=3)
            db.add(session)
            db.commit()
            assert actions.conversation(db, p, incoming, "12345").id != session.id
            assert actions.conversation(db, p, incoming, "54321").id != session.id
        finally:
            await adapter.close()

    asyncio.run(run())
    assert telegram_server["requests"]


def test_render_full_preview_chunks_and_overflow(
    demo: tuple, db: Session, telegram_server: dict
) -> None:
    p, _, incoming, session, _, response = preview(db, demo)
    card = response.messages[0]
    card.items[0].after.owner_statement = "长" * 9000
    body, offered = rendering.render(card)
    assert "本人陈述" not in body and "长" not in body and offered
    card.items[0].after.after.statement = "长" * 9000

    async def run() -> None:
        adapter = Telegram(p.org_id, {"bot_token": TOKEN})
        try:
            await actions.deliver(
                db, p, incoming, "12345", actions.Reply(session.id, response), adapter
            )
        finally:
            await adapter.close()

    asyncio.run(run())
    sends = [
        body for method, body in telegram_server["requests"] if method == "sendMessage"
    ]
    assert len(sends) == 4 and all(len(m["text"]) <= 3000 for m in sends)
    assert all("reply_markup" not in m for m in sends[:-1])
    assert len(sends[-1]["reply_markup"]["inline_keyboard"][0]) == 2
    card.items[0].after.after.statement = "长" * 12000
    assert rendering.render(card) == (
        "预览内容较长，请在网页查看完整内容并确认提交。",
        [],
    )


def test_im_action_migration(migration_engine: Engine) -> None:
    migrate(migration_engine, "downgrade", "b123001bc001")
    with migration_engine.connect() as connection:
        assert "im_action" not in inspect(connection).get_table_names()
    migrate(migration_engine, "upgrade", "b141001bc001")
    with migration_engine.connect() as connection:
        schema = inspect(connection)
        assert {
            tuple(fk["constrained_columns"])
            for fk in schema.get_foreign_keys("im_action")
        } >= {("org_id", "employee_id"), ("org_id", "session_id")}
        assert any(
            fk["constrained_columns"] == ["org_id", "pending_question_id"]
            for fk in schema.get_foreign_keys("bot_session")
        )
        assert connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).all() == [("b141001bc001",)]


def test_closed_question_does_not_trap_conversation(demo: tuple, db: Session) -> None:
    p, _ = setup(db, demo)
    incoming = event(p.org_id, "本人合成回答")
    session = actions.conversation(db, p, incoming, "12345")
    question = PendingQuestion(
        org_id=p.org_id,
        target_employee_id=p.employee_id,
        question="还在使用这项能力吗？",
        reason="定期确认",
    )
    db.add(question)
    db.flush()
    session.pending_question_id = question.id
    db.add(session)
    db.commit()
    # The web entry answers after the IM answer button was selected.
    answer_question(p, db, question.id, "已在网页确认的合成回答")
    db.commit()
    reply = actions.text(db, p, incoming, "12345")
    db.refresh(session)
    assert session.pending_question_id is None
    assert "其他入口处理" in reply.response.messages[0].text
    resumed = actions.text(db, p, incoming, "12345")
    assert "先配置模型" in resumed.response.messages[0].text


def test_file_does_not_answer_pending_question(demo: tuple, db: Session) -> None:
    p, _ = setup(db, demo)
    incoming = replace(
        event(p.org_id, ""),
        platform="feishu",
        text=None,
        attachment=Attachment("file", "材料.txt", "合成材料".encode()),
    )
    session = actions.conversation(db, p, incoming, "synthetic-app")
    question = PendingQuestion(
        org_id=p.org_id,
        target_employee_id=p.employee_id,
        question="还在使用这项能力吗？",
        reason="定期确认",
    )
    db.add(question)
    db.flush()
    session.pending_question_id = question.id
    db.add(session)
    db.commit()
    actions.text(db, p, incoming, "synthetic-app")
    db.refresh(question)
    db.refresh(session)
    assert question.status == "open" and session.pending_question_id == question.id
    history = store.messages_for(db, session)
    assert history[0].payload["text"] == "我发送了文件：材料.txt"
    assert history[0].payload["attachments"][0]["filename"] == "材料.txt"
