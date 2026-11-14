import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from pydantic import TypeAdapter
from sqlmodel import Session, col, select

from app.authz.principal import Principal
from app.domains.base import utc_now
from app.domains.bot import attachments, core, store
from app.domains.bot import schemas as s
from app.domains.bot.models import BotMessage, BotSession
from app.domains.bot.settings import read_settings
from app.domains.im import rendering
from app.domains.im.adapters.base import Adapter, Event
from app.domains.im.models import ImAction
from app.domains.inbox.service import read_question


@dataclass
class Reply:
    session_id: UUID
    response: s.TurnResponse
    clear_buttons: bool = False


def conversation(db: Session, p: Principal, event: Event, identity: str) -> BotSession:
    row = db.exec(
        select(BotSession)
        .where(
            BotSession.org_id == p.org_id,
            BotSession.employee_id == p.employee_id,
            BotSession.channel == event.platform,
            BotSession.external_chat_id == event.external_chat_id,
            BotSession.bot_identity == identity,
            BotSession.last_activity_at > utc_now() - timedelta(hours=2),
        )
        .order_by(col(BotSession.last_activity_at).desc())
        .limit(1)
    ).first()
    return row or store.create_session(
        db, p, event.platform, event.external_chat_id, identity
    )


def text(
    db: Session,
    p: Principal,
    event: Event,
    identity: str,
    *,
    on_started: Callable[[], None] | None = None,
) -> Reply:
    session = conversation(db, p, event, identity)
    attachment_ids = []
    text = event.text or ""
    if event.attachment:
        attachment = attachments.save_bytes(
            db,
            p,
            "bot",
            session.id,
            event.attachment.filename,
            event.attachment.content,
        )
        attachment_ids = [attachment.id]
        text = text or f"我发送了文件：{attachment.filename}"
    body = s.TurnInput(text=text)
    if session.pending_question_id and not attachment_ids:
        question = read_question(p, db, session.pending_question_id)
        if question.status != "open":
            session.pending_question_id = None
            db.add(session)
            db.commit()
            return Reply(
                session.id,
                s.TurnResponse(
                    messages=[
                        s.TextMessage(
                            text="该问题已在其他入口处理，请重新发送消息继续对话。"
                        )
                    ]
                ),
            )
        response = core.act(
            db,
            p,
            session.id,
            s.QuestionAction(
                action="answer_question",
                question_id=session.pending_question_id,
                answer=body.text,
            ),
            commit=False,
        )
        if not session.lease_until or session.lease_until <= utc_now():
            session.pending_question_id = None
            db.add(session)
        db.commit()
    else:
        response = core.turn(
            db,
            p,
            session.id,
            body.text,
            attachment_ids=attachment_ids,
            on_started=on_started,
        )
    return Reply(session.id, response)


def callback(db: Session, p: Principal, event: Event, identity: str) -> Reply | str:
    row = db.exec(
        select(ImAction)
        .where(
            ImAction.token == event.token,
            ImAction.org_id == p.org_id,
            ImAction.platform == event.platform,
            ImAction.bot_identity == identity,
            ImAction.employee_id == p.employee_id,
            ImAction.external_chat_id == event.external_chat_id,
            ImAction.message_id == event.message_id,
        )
        .with_for_update()
    ).one_or_none()
    if row is None or row.expires_at <= utc_now():
        return "按钮无效或已过期，请重新查看。"
    if row.consumed_at:
        return Reply(
            row.session_id,
            s.TurnResponse(messages=[s.TextMessage(text="已处理")]),
            True,
        )
    session = store.get_session(db, p, row.session_id, lock=True)
    if (
        session.channel != event.platform
        or session.bot_identity != identity
        or session.external_chat_id != event.external_chat_id
    ):
        return "按钮无效或已过期，请重新查看。"
    if session.lease_until and session.lease_until > utc_now():
        return "忙，请稍后再试"
    if session.last_activity_at <= utc_now() - timedelta(hours=2):
        return "会话已过期，请重新开始对话。"
    if row.action["action"] == "answer_question":
        question_id = UUID(str(row.action["question_id"]))
        cards = [
            card
            for m in store.list_messages(db, p, session.id)
            if m.role == "assistant" and isinstance(m.payload, dict)
            for card in s.TurnResponse.model_validate(m.payload).messages
        ]
        if not any(
            isinstance(c, s.QuestionCard)
            and c.id == question_id
            and "answer_question" in c.actions
            for c in cards
        ):
            return "请重新查看问题。"
        read_question(p, db, question_id)
        session.pending_question_id = question_id
        session.last_activity_at = utc_now()
        db.add(session)
        response = s.TurnResponse(messages=[s.TextMessage(text="请发送你的回答")])
    else:
        response = core.act(
            db,
            p,
            session.id,
            TypeAdapter(s.Action).validate_python(row.action),
            client_name=f"内置机器人（{ {'telegram': 'Telegram', 'feishu': '飞书', 'wecom': '企业微信'}[event.platform] }）",
            commit=False,
        )
    # Commit consumption with the core's business writes, never before them.
    row.consumed_at = utc_now()
    db.add(row)
    db.commit()
    return Reply(session.id, response, True)


async def deliver(
    db: Session,
    p: Principal,
    event: Event,
    identity: str,
    reply: Reply,
    adapter: Adapter,
) -> None:
    session = store.get_session(db, p, reply.session_id)
    days = read_settings(db, p.org_id).retention_days
    created_at = db.exec(
        select(BotMessage.created_at)
        .where(
            BotMessage.org_id == p.org_id,
            BotMessage.session_id == session.id,
            BotMessage.role == "assistant",
        )
        .order_by(col(BotMessage.created_at).desc())
        .limit(1)
    ).first()
    expires = (created_at or session.last_activity_at) + (
        timedelta(days=days) if days else timedelta(hours=2)
    )
    for message in reply.response.messages:
        body, offered = rendering.render(message)
        rows = [
            ImAction(
                token=secrets.token_urlsafe(16),
                org_id=p.org_id,
                platform=event.platform,
                bot_identity=identity,
                employee_id=session.employee_id,
                session_id=session.id,
                external_chat_id=event.external_chat_id,
                message_id="",
                action=action,
                expires_at=expires,
            )
            for action in offered
        ]
        message_id = await adapter.render(
            event.external_chat_id,
            body,
            [(rendering.LABELS[str(r.action["action"])], r.token) for r in rows],
        )
        for row in rows:
            row.message_id = message_id
            db.add(row)
        db.commit()
