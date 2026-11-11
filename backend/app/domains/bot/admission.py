from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime, timedelta
from hashlib import blake2b
from threading import Event

from pydantic import JsonValue
from sqlalchemy import text
from sqlmodel import Session, col, select, update

from app.authz.principal import Principal
from app.domains.admin_assistant import store as admin_store
from app.domains.admin_assistant.models import (
    AdminAssistantMessage,
    AdminAssistantSession,
)
from app.domains.base import utc_now
from app.domains.bot.models import BotMessage, BotSession
from app.domains.bot.store import append_message, get_session
from app.domains.drafting.service import drafting_jobs
from app.domains.model.client import ModelRateLimited
from app.domains.model.models import ModelSetting
from app.domains.model.pi_client import PiBusy, TurnCancelled

LEASE_SECONDS = 75
ORG_TURNS = 2
HOURLY_TURNS = 30


class BotBusy(Exception):
    pass


class BotDraftingPriority(Exception):
    pass


class BotRateLimited(Exception):
    def __init__(self, retry_at: datetime) -> None:
        self.retry_at = retry_at
        super().__init__("对话次数已达上限，请稍后再试")


class BotQuotaPaused(Exception):
    def __init__(self, retry_at: datetime, estimated: bool) -> None:
        self.retry_at, self.estimated = retry_at, estimated
        super().__init__("模型额度暂时用完")


@contextmanager
def begin_turn(
    db: Session,
    principal: Principal,
    session: BotSession | AdminAssistantSession,
    user_payload: JsonValue,
    cancelled: Event | None = None,
) -> Generator[BotSession | AdminAssistantSession]:
    lease = None
    admin = isinstance(session, AdminAssistantSession)
    session_type = AdminAssistantSession if admin else BotSession
    message_type = AdminAssistantMessage if admin else BotMessage
    try:
        key = int.from_bytes(
            blake2b(b"bot:" + principal.org_id.bytes, digest_size=8).digest(),
            signed=True,
        )
        db.connection().execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": key}
        )
        row = (
            admin_store.get_session(db, principal, session.id, lock=True)
            if admin
            else get_session(db, principal, session.id, lock=True)
        )
        now = utc_now()
        setting = db.get(ModelSetting, principal.org_id, populate_existing=True)
        if setting and setting.paused_until and setting.paused_until > now:
            raise BotQuotaPaused(
                setting.paused_until, "估计" in (setting.pause_reason or "")
            )
        if (
            setting
            and setting.kind == "chatgpt_subscription"
            and drafting_jobs(db, principal.org_id)
        ):
            raise BotDraftingPriority("起草任务正在运行，请稍后再试")
        if drafting_jobs(db, queued_only=True):
            raise BotBusy("起草任务正在排队，请稍后再试")
        if row.lease_until and row.lease_until > now:
            raise BotBusy("本会话已有一轮对话")
        active = sum(
            len(
                db.exec(
                    select(model.id)
                    .where(
                        model.org_id == principal.org_id, col(model.lease_until) > now
                    )
                    .limit(ORG_TURNS)
                ).all()
            )
            for model in (BotSession, AdminAssistantSession)
        )
        if active >= ORG_TURNS:
            raise BotBusy("本企业对话繁忙，请稍后再试")
        owner = (
            AdminAssistantSession.user_id == principal.user_id
            if admin
            else BotSession.employee_id == principal.employee_id
        )
        times = db.exec(
            select(message_type.created_at)
            .join(
                session_type,
                (col(session_type.org_id) == col(message_type.org_id))
                & (col(session_type.id) == col(message_type.session_id)),
            )
            .where(
                message_type.org_id == principal.org_id,
                owner,
                message_type.role == "user",
                message_type.created_at > now - timedelta(hours=1),
            )
            .order_by(col(message_type.created_at))
            .limit(HOURLY_TURNS)
        ).all()
        reservations = (
            db.exec(
                select(session_type.id).where(
                    session_type.org_id == principal.org_id,
                    owner,
                    col(session_type.lease_until) > now,
                    ~select(message_type.id)
                    .where(
                        message_type.org_id == principal.org_id,
                        message_type.session_id == session_type.id,
                        message_type.role == "user",
                        message_type.created_at
                        >= col(session_type.lease_until)
                        - timedelta(seconds=LEASE_SECONDS),
                    )
                    .exists(),
                )
            ).all()
            if active
            else []
        )
        if len(times) + len(reservations) >= HOURLY_TURNS:
            raise BotRateLimited(
                times[0] + timedelta(hours=1)
                if times
                else now + timedelta(seconds=LEASE_SECONDS)
            )
        new_lease = now + timedelta(seconds=LEASE_SECONDS)
        row.lease_until = new_lease
        if cancelled is None:
            row.turn_count += 1
            append_message(db, row, "user", user_payload)
        db.commit()
        lease = new_lease
        if cancelled is not None:
            row.turn_count += 1
            append_message(db, row, "user", user_payload)
        yield row
        if cancelled is not None and cancelled.is_set():
            raise TurnCancelled()
        db.commit()
    except ModelRateLimited as error:
        db.rollback()
        retry_at = error.retry_at or utc_now() + timedelta(minutes=30)
        estimated = error.retry_at is None or "估计" in error.reason
        setting = db.exec(
            select(ModelSetting)
            .where(
                ModelSetting.org_id == principal.org_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one_or_none()
        if setting:
            if setting.paused_until and setting.paused_until > retry_at:
                retry_at = setting.paused_until
                estimated = "估计" in (setting.pause_reason or "")
            setting.paused_until = retry_at
            setting.pause_reason = "恢复时间为估计" if estimated else "模型用量到上限"
            db.add(setting)
        db.commit()
        raise BotQuotaPaused(retry_at, estimated) from None
    except PiBusy:
        db.rollback()
        raise BotBusy("模型服务繁忙，请稍后再试") from None
    except BaseException:
        db.rollback()
        raise
    finally:
        if lease is not None:
            # An expired turn must not release a successor's lease.
            db.exec(
                update(session_type)
                .where(
                    col(session_type.org_id) == principal.org_id,
                    col(session_type.id) == session.id,
                    col(session_type.lease_until) == lease,
                )
                .values(lease_until=None)
            )
            db.commit()
