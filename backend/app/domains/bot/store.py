import json
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, JsonValue
from sqlmodel import Session, col, delete, select

from app.authz.principal import Principal
from app.core.secrets import decrypt_secret, encrypt_secret
from app.domains.admin_assistant.models import (
    AdminAssistantMessage,
    AdminAssistantSession,
)
from app.domains.base import utc_now
from app.domains.bot.models import BotMessage, BotSession, ConversationMessage
from app.domains.bot.settings import read_settings
from app.domains.model.pi_client import HistoryMessage
from app.domains.org.models import Organization
from app.domains.org.service import visible_employee

HISTORY_MESSAGES = 20
HISTORY_CHARS = 24000


class BotSessionNotFound(HTTPException):
    def __init__(self) -> None:
        super().__init__(404, "会话不存在")


class MessagePublic(BaseModel):
    id: UUID
    role: Literal["user", "assistant"]
    payload: JsonValue
    created_at: datetime


def create_session(
    db: Session,
    principal: Principal,
    channel: Literal["web", "telegram", "feishu", "wecom"] = "web",
    external_chat_id: str | None = None,
    bot_identity: str | None = None,
) -> BotSession:
    if principal.employee_id is None:
        raise BotSessionNotFound()
    visible_employee(db, principal, principal.employee_id)
    row = BotSession(
        org_id=principal.org_id,
        employee_id=principal.employee_id,
        channel=channel,
        external_chat_id=external_chat_id,
        bot_identity=bot_identity,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_session(
    db: Session, principal: Principal, session_id: UUID, *, lock: bool = False
) -> BotSession:
    query = (
        select(BotSession)
        .where(
            BotSession.org_id == principal.org_id,
            BotSession.employee_id == principal.employee_id,
            BotSession.id == session_id,
        )
        .execution_options(populate_existing=True)
    )
    row = db.exec(query.with_for_update() if lock else query).one_or_none()
    if row is None:
        raise BotSessionNotFound()
    visible_employee(db, principal, row.employee_id)
    return row


def append_message(
    db: Session,
    session: BotSession | AdminAssistantSession,
    role: Literal["user", "assistant"],
    payload: JsonValue,
) -> ConversationMessage:
    message_type: type[ConversationMessage] = (
        AdminAssistantMessage
        if isinstance(session, AdminAssistantSession)
        else BotMessage
    )
    if not read_settings(
        db, session.org_id
    ).retention_days and session.last_activity_at <= utc_now() - timedelta(hours=2):
        db.exec(
            delete(message_type).where(
                col(message_type.org_id) == session.org_id,
                col(message_type.session_id) == session.id,
            )
        )
    row = message_type(
        org_id=session.org_id,
        session_id=session.id,
        role=role,
        ciphertext=encrypt_secret(json.dumps(payload, ensure_ascii=False)),
    )
    db.add(row)
    session.last_activity_at = utc_now()
    db.add(session)
    db.flush()
    return row


def _messages(
    db: Session,
    session: BotSession | AdminAssistantSession,
    *,
    limit: int | None = None,
) -> Sequence[ConversationMessage]:
    message_type: type[ConversationMessage] = (
        AdminAssistantMessage
        if isinstance(session, AdminAssistantSession)
        else BotMessage
    )
    days = read_settings(db, session.org_id).retention_days
    now = utc_now()
    if not days and session.last_activity_at <= now - timedelta(hours=2):
        return []
    query = (
        select(message_type)
        .where(
            message_type.org_id == session.org_id,
            message_type.session_id == session.id,
            message_type.created_at > now - timedelta(days=days) if days else True,
        )
        .order_by(col(message_type.created_at).desc(), col(message_type.id).desc())
    )
    return list(db.exec(query.limit(limit) if limit else query).all())[::-1]


def list_messages(
    db: Session, principal: Principal, session_id: UUID
) -> list[MessagePublic]:
    return messages_for(db, get_session(db, principal, session_id))


def messages_for(
    db: Session, session: BotSession | AdminAssistantSession
) -> list[MessagePublic]:
    return [
        MessagePublic(
            id=row.id,
            role=row.role,
            payload=json.loads(decrypt_secret(row.ciphertext)),
            created_at=row.created_at,
        )
        for row in _messages(db, session)
    ]


def load_history(
    db: Session,
    session: BotSession | AdminAssistantSession,
    *,
    transform: Callable[[str], str] | None = None,
) -> list[HistoryMessage]:
    history: list[HistoryMessage] = []
    remaining = HISTORY_CHARS
    for row in reversed(_messages(db, session, limit=HISTORY_MESSAGES)):
        text = decrypt_secret(row.ciphertext)
        if transform:
            text = transform(text)
        history.append(HistoryMessage(role=row.role, text=text[-remaining:]))
        remaining -= len(history[-1].text)
        if not remaining:
            break
    return history[::-1]


def cleanup_messages(db: Session, now: datetime) -> None:
    for org_id in db.exec(select(Organization.id)).all():
        days = read_settings(db, org_id).retention_days
        for session_type, message_type in (
            (BotSession, BotMessage),
            (AdminAssistantSession, AdminAssistantMessage),
        ):
            expired = select(session_type.id).where(
                session_type.org_id == org_id,
                session_type.last_activity_at <= now - timedelta(hours=2),
            )
            db.exec(
                delete(message_type).where(
                    col(message_type.org_id) == org_id,
                    col(message_type.created_at) <= now - timedelta(days=days)
                    if days
                    else col(message_type.session_id).in_(expired),
                )
            )
    db.commit()
