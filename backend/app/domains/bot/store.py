import json
import logging
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, JsonValue
from sqlalchemy import case, false, true
from sqlmodel import Session, col, delete, select, update

from app.authz.principal import Principal
from app.core.secrets import decrypt_secret, encrypt_secret
from app.domains.admin_assistant.models import (
    AdminAssistantAttachment,
    AdminAssistantMessage,
    AdminAssistantSession,
    AdminChangeSet,
)
from app.domains.base import utc_now
from app.domains.bot.models import (
    BotAttachment,
    BotMessage,
    BotSession,
    ConversationMessage,
)
from app.domains.bot.settings import read_settings
from app.domains.capacity.settings import read_settings as read_capacity_settings
from app.domains.org.models import Organization
from app.domains.org.service import visible_employee

logger = logging.getLogger(__name__)

HIDDEN_REPLY = "你的权限已变化，这条之前的回复已隐藏"
SENSITIVE_TOOLS = {
    "coordination",
    "prepare_plan_proposal",
    "get_demand_basis",
    "list_team_members",
    "find_people",
    "get_member_profile",
    "prepare_profile_edit",
    "查看团队成员",
    "按能力找人",
    "查看下属画像",
    "预览直接修改画像",
    "调查并模拟部门协调方案",
    "预览部门方案答复",
    "查看本部门需求依据和个人负荷，仅限答复方",
}


class BotSessionNotFound(HTTPException):
    def __init__(self) -> None:
        super().__init__(404, "会话不存在")


class MessagePublic(BaseModel):
    id: UUID
    role: Literal["user", "assistant"]
    payload: JsonValue
    created_at: datetime


class SessionSummary(BaseModel):
    id: UUID
    started_at: datetime
    last_activity_at: datetime
    turn_count: int
    preview: str
    purpose: Literal["setup"] | None = None


class SessionList(BaseModel):
    data: list[SessionSummary]
    retention_days: int


def list_sessions(
    db: Session, principal: Principal, *, administrator: bool = False
) -> SessionList:
    days = read_settings(db, principal.org_id).retention_days
    session_type: type[BotSession] | type[AdminAssistantSession]
    message_type: type[BotMessage] | type[AdminAssistantMessage]
    if administrator:
        if not principal.is_org_admin or principal.user_id is None:
            raise BotSessionNotFound()
        session_type = AdminAssistantSession
        message_type = AdminAssistantMessage
        owner = col(AdminAssistantSession.user_id) == principal.user_id
    else:
        if principal.employee_id is None:
            raise BotSessionNotFound()
        visible_employee(db, principal, principal.employee_id)
        session_type = BotSession
        message_type = BotMessage
        owner = (col(BotSession.employee_id) == principal.employee_id) & (
            col(BotSession.channel) == "web"
        )
    from app.domains.setup.wizard import read_setup

    pending_setup = administrator and not read_setup(db, principal.org_id).completed
    pinned = (
        (col(AdminAssistantSession.purpose) == "setup") if pending_setup else false()
    )
    now = utc_now()
    retained = select(message_type.id).where(
        message_type.org_id == principal.org_id,
        message_type.session_id == session_type.id,
        message_type.created_at > now - timedelta(days=days) if days else True,
    )
    first_user = (
        select(message_type.ciphertext)
        .where(
            message_type.org_id == principal.org_id,
            message_type.session_id == session_type.id,
            message_type.role == "user",
            message_type.created_at > now - timedelta(days=days) if days else True,
        )
        .order_by(col(message_type.created_at), col(message_type.id))
        .limit(1)
        .scalar_subquery()
    )
    rows = db.exec(
        select(session_type, first_user)
        .where(
            col(session_type.org_id) == principal.org_id,
            owner,
            retained.exists() | pinned,
            (col(session_type.last_activity_at) > now - timedelta(hours=2)) | pinned
            if not days
            else True,
        )
        .order_by(
            case((pinned, 0), else_=1),
            col(session_type.last_activity_at).desc(),
            col(session_type.id).desc(),
        )
        .limit(50)
    ).all()
    return SessionList(
        retention_days=days,
        data=[
            SessionSummary(
                **row.model_dump(
                    include={"id", "started_at", "last_activity_at", "turn_count"}
                ),
                purpose="setup"
                if isinstance(row, AdminAssistantSession) and row.purpose == "setup"
                else None,
                preview=json.loads(decrypt_secret(ciphertext))["text"][:40]
                if ciphertext
                else "",
            )
            for row, ciphertext in rows
        ],
    )


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
            col(BotSession.employee_id) == principal.employee_id,
            BotSession.id == session_id,
        )
        .execution_options(populate_existing=True)
    )
    row = db.exec(query.with_for_update() if lock else query).one_or_none()
    if row is None:
        raise BotSessionNotFound()
    visible_employee(db, principal, row.employee_id)
    return row


def setup_in_progress(db: Session, session: BotSession | AdminAssistantSession) -> bool:
    from app.domains.setup.wizard import read_setup

    return (
        isinstance(session, AdminAssistantSession)
        and session.purpose == "setup"
        and not read_setup(db, session.org_id).completed
    )


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
    if (
        not read_settings(db, session.org_id).retention_days
        and not setup_in_progress(db, session)
        and session.last_activity_at <= utc_now() - timedelta(hours=2)
    ):
        session.state_ciphertext = None
        attachment_type = (
            AdminAssistantAttachment
            if isinstance(session, AdminAssistantSession)
            else BotAttachment
        )
        db.exec(
            delete(attachment_type).where(
                col(attachment_type.org_id) == session.org_id,
                col(attachment_type.session_id) == session.id,
                col(attachment_type.created_at) <= utc_now() - timedelta(hours=2),
            )
        )
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
    protected = setup_in_progress(db, session)
    if (
        not days
        and not protected
        and session.last_activity_at <= now - timedelta(hours=2)
    ):
        return []
    query = (
        select(message_type)
        .where(
            message_type.org_id == session.org_id,
            message_type.session_id == session.id,
            message_type.created_at > now - timedelta(days=days)
            if days and not protected
            else True,
        )
        .order_by(col(message_type.created_at).desc(), col(message_type.id).desc())
    )
    return list(db.exec(query.limit(limit) if limit else query).all())[::-1]


def list_messages(
    db: Session, principal: Principal, session_id: UUID
) -> list[MessagePublic]:
    session = get_session(db, principal, session_id)
    messages = messages_for(db, session)
    current = session_scope(db, principal)
    legacy_sensitive = (
        _legacy_sensitive(messages, load_state(db, session))
        if any(
            message.role == "assistant"
            and isinstance(message.payload, dict)
            and "scope" not in message.payload
            for message in messages
        )
        else False
    )
    for message in messages:
        if message.role != "assistant" or not isinstance(message.payload, dict):
            continue
        previous = message.payload.get("scope")
        if scope_shrank(previous, current, legacy_sensitive=legacy_sensitive):
            message.payload = {"messages": [{"type": "text", "text": HIDDEN_REPLY}]}
        else:
            message.payload.pop("scope", None)
    return messages


def session_scope(db: Session, principal: Principal) -> dict[str, JsonValue]:
    departments: list[JsonValue] = []
    departments.extend(sorted(str(id) for id in principal.managed_department_ids))
    return {
        "is_org_admin": principal.is_org_admin,
        "managed_department_ids": departments,
        "capacity_enabled": read_capacity_settings(principal, db).enabled,
    }


def scope_shrank(
    previous: JsonValue | None,
    current: dict[str, JsonValue],
    *,
    legacy_sensitive: bool = False,
) -> bool:
    if not isinstance(previous, dict):
        return legacy_sensitive
    old_departments = previous.get("managed_department_ids")
    new_departments = current["managed_department_ids"]
    return bool(
        (previous.get("is_org_admin") and not current["is_org_admin"])
        or (previous.get("capacity_enabled") and not current["capacity_enabled"])
        or (
            isinstance(old_departments, list)
            and isinstance(new_departments, list)
            and not {str(id) for id in old_departments}.issubset(new_departments)
        )
    )


def _legacy_sensitive(
    messages: list[MessagePublic], state: dict[str, JsonValue] | None
) -> bool:
    for message in messages:
        if message.role == "assistant" and isinstance(message.payload, dict):
            cards = message.payload.get("messages")
            if isinstance(cards, list) and any(
                isinstance(item, dict) and item.get("type") == "plan_card"
                for item in cards
            ):
                return True
            steps = message.payload.get("steps")
            if isinstance(steps, list) and any(
                isinstance(step, dict) and step.get("name") in SENSITIVE_TOOLS
                for step in steps
            ):
                return True
    serialized = json.dumps(state, ensure_ascii=False) if state else ""
    return any(f'"{name}"' in serialized for name in SENSITIVE_TOOLS)


def state_for_turn(
    db: Session, principal: Principal, session: BotSession
) -> dict[str, JsonValue] | None:
    saved = load_state(db, session, scoped=True)
    if saved is None:
        return None
    if "scope" in saved:
        previous = saved["scope"]
        state = {key: value for key, value in saved.items() if key != "scope"}
        legacy_sensitive = False
    else:
        previous = None
        state = saved
        legacy_sensitive = _legacy_sensitive(messages_for(db, session), saved)
    if scope_shrank(
        previous, session_scope(db, principal), legacy_sensitive=legacy_sensitive
    ):
        save_state(db, session, None)
        db.commit()
        return None
    return state if isinstance(state, dict) else None


def save_scoped_state(
    db: Session,
    principal: Principal,
    session: BotSession,
    state: dict[str, JsonValue] | None,
) -> None:
    save_state(
        db,
        session,
        {**state, "scope": session_scope(db, principal)} if state is not None else None,
    )


def messages_for(
    db: Session,
    session: BotSession | AdminAssistantSession,
    *,
    limit: int | None = None,
) -> list[MessagePublic]:
    return [
        MessagePublic(
            id=row.id,
            role=row.role,
            payload=json.loads(decrypt_secret(row.ciphertext)),
            created_at=row.created_at,
        )
        for row in _messages(db, session, limit=limit)
    ]


def load_state(
    db: Session, session: BotSession | AdminAssistantSession, *, scoped: bool = False
) -> dict[str, JsonValue] | None:
    days = read_settings(db, session.org_id).retention_days
    if not days and session.last_activity_at <= utc_now() - timedelta(hours=2):
        return None
    message_type = (
        AdminAssistantMessage
        if isinstance(session, AdminAssistantSession)
        else BotMessage
    )
    if (
        days
        and db.exec(
            select(message_type.id)
            .where(
                message_type.org_id == session.org_id,
                message_type.session_id == session.id,
                message_type.created_at <= utc_now() - timedelta(days=days),
            )
            .limit(1)
        ).first()
    ):
        return None
    attachment_type = (
        AdminAssistantAttachment
        if isinstance(session, AdminAssistantSession)
        else BotAttachment
    )
    if (
        session.state_ciphertext
        and db.exec(
            select(attachment_type.id)
            .where(
                attachment_type.org_id == session.org_id,
                attachment_type.session_id == session.id,
                attachment_type.created_at
                <= utc_now() - (timedelta(days=days) if days else timedelta(hours=2)),
            )
            .limit(1)
        ).first()
    ):
        return None
    state = (
        json.loads(decrypt_secret(session.state_ciphertext))
        if session.state_ciphertext
        else None
    )
    if isinstance(session, BotSession) and state and "scope" in state and not scoped:
        return {key: value for key, value in state.items() if key != "scope"}
    return state


def save_state(
    db: Session,
    session: BotSession | AdminAssistantSession,
    state: dict[str, JsonValue] | None,
) -> None:
    session.state_ciphertext = (
        encrypt_secret(json.dumps(state, ensure_ascii=False))
        if state is not None
        else None
    )
    db.add(session)


def cleanup_messages(db: Session, now: datetime) -> None:
    org_ids = db.exec(select(Organization.id)).all()
    db.commit()
    for org_id in org_ids:
        try:
            from app.domains.setup.wizard import read_setup

            days = read_settings(db, org_id).retention_days
            pending_setup = not read_setup(db, org_id).completed
            for session_type, message_type, attachment_type in (
                (BotSession, BotMessage, BotAttachment),
                (
                    AdminAssistantSession,
                    AdminAssistantMessage,
                    AdminAssistantAttachment,
                ),
            ):
                # Extracted file text can also exist in encrypted model context.
                db.exec(
                    update(session_type)
                    .where(
                        col(session_type.org_id) == org_id,
                        col(session_type.id).in_(
                            select(attachment_type.session_id).where(
                                attachment_type.org_id == org_id,
                                attachment_type.created_at
                                <= now
                                - (
                                    timedelta(days=days) if days else timedelta(hours=2)
                                ),
                            )
                        ),
                    )
                    .values(state_ciphertext=None)
                )
                db.exec(
                    delete(attachment_type).where(
                        col(attachment_type.org_id) == org_id,
                        col(attachment_type.created_at)
                        <= now - (timedelta(days=days) if days else timedelta(hours=2)),
                    )
                )
                expired = select(session_type.id).where(
                    session_type.org_id == org_id,
                    session_type.last_activity_at <= now - timedelta(hours=2),
                )
                expired_messages = select(message_type.session_id).where(
                    message_type.org_id == org_id,
                    message_type.created_at <= now - timedelta(days=days),
                )
                db.exec(
                    update(session_type)
                    .where(
                        col(session_type.org_id) == org_id,
                        col(session_type.id).in_(expired_messages if days else expired),
                    )
                    .values(state_ciphertext=None)
                )
                if session_type is AdminAssistantSession:
                    db.exec(
                        update(AdminChangeSet)
                        .where(
                            col(AdminChangeSet.org_id) == org_id,
                            col(AdminChangeSet.session_id).in_(
                                select(AdminAssistantSession.id).where(
                                    AdminAssistantSession.org_id == org_id,
                                    AdminAssistantSession.last_activity_at
                                    <= now
                                    - (
                                        timedelta(days=days)
                                        if days
                                        else timedelta(hours=2)
                                    ),
                                )
                            ),
                            col(AdminChangeSet.status) == "draft",
                        )
                        .values(
                            secrets=None,
                            status="stale",
                            confirmed_once_at=None,
                            updated_at=now,
                        )
                    )
                protected = select(AdminAssistantSession.id).where(
                    AdminAssistantSession.org_id == org_id,
                    AdminAssistantSession.purpose == "setup",
                    pending_setup,
                )
                db.exec(
                    delete(message_type).where(
                        ~col(message_type.session_id).in_(protected)
                        if session_type is AdminAssistantSession
                        else true(),
                        col(message_type.org_id) == org_id,
                        col(message_type.created_at) <= now - timedelta(days=days)
                        if days
                        else col(message_type.session_id).in_(expired),
                    )
                )
            db.commit()
        except Exception as error:
            db.rollback()
            logger.error("org_id=%s error_type=%s", org_id, type(error).__name__)
