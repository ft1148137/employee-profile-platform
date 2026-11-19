import asyncio
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import partial
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import Engine
from sqlmodel import Session, col, select, update

from app.authz.principal import Principal
from app.core.config import settings
from app.domains.base import utc_now
from app.domains.im.adapters.base import Adapter, ImError
from app.domains.im.models import ImBinding, ImNotification, ImSetting
from app.domains.im.schemas import Platform
from app.domains.inbox.models import InboxItem, InboxKind
from app.domains.org.models import Organization, OrgMembership
from app.worker.health import WORKER_ALERT_NOTIFICATION, WORKER_ALERT_PREFIX

# Most kinds use fixed copy because inbox messages may contain private data.
REMINDERS = {
    InboxKind.CAPACITY: "你有需求或负荷事项需要处理",
    InboxKind.INITIAL_REVIEW: "你有新的画像请求待过目",
    InboxKind.PROFILE_EDITED: "你的画像已被直接修改",
    InboxKind.SUBMITTED: "你的画像请求已提交",
    InboxKind.STATUS_CHANGED: "你的画像请求状态已更新",
    InboxKind.QUESTION: "你有待确认问题或定期提醒",
    InboxKind.DICTIONARY_CHANGED: "能力词典已更新",
    InboxKind.REVIEW_REQUESTED: "你有新的待审批请求",
    InboxKind.DECIDED: "你的画像请求有新的审批结果",
}


def enqueue(
    db: Session, item: InboxItem, *, borrow_assignment_id: UUID | None = None
) -> None:
    employee_id = item.recipient_employee_id
    if employee_id is None and item.recipient_user_id:
        employee_id = db.exec(
            select(OrgMembership.employee_id).where(
                OrgMembership.org_id == item.org_id,
                OrgMembership.user_id == item.recipient_user_id,
            )
        ).one_or_none()
    if employee_id is None:
        return
    for row in db.exec(
        select(ImBinding).where(
            ImBinding.org_id == item.org_id,
            ImBinding.employee_id == employee_id,
            ImBinding.notify == True,  # noqa: E712
        )
    ).all():
        if item.kind == InboxKind.ADMIN_TODOS and row.platform != "feishu":
            continue
        db.add(
            ImNotification(
                org_id=item.org_id,
                inbox_item_id=item.id,
                borrow_assignment_id=borrow_assignment_id,
                employee_id=employee_id,
                platform=row.platform,
            )
        )


def cancel_pending(
    db: Session, org_id: UUID, platform: Platform, employee_id: UUID | None = None
) -> None:
    statement = update(ImNotification).where(
        col(ImNotification.org_id) == org_id,
        col(ImNotification.platform) == platform,
        col(ImNotification.status) == "pending",
    )
    if employee_id is not None:
        statement = statement.where(col(ImNotification.employee_id) == employee_id)
    db.exec(statement.values(status="failed", last_error_code="cancelled"))


@dataclass
class Pending:
    platform: Platform
    credentials: str | None = field(repr=False)
    chat_id: str
    text: str
    attempted: bool = False
    error: str | None = None


def organizations(engine: Engine) -> list[UUID]:
    with Session(engine) as db:
        return list(db.exec(select(Organization.id)).all())


def pending_ids(engine: Engine, org_id: UUID) -> list[UUID]:
    with Session(engine) as db:
        ids: list[UUID] = []
        for platform in ("telegram", "feishu", "wecom"):
            ids.extend(
                db.exec(
                    select(ImNotification.id)
                    .where(
                        ImNotification.org_id == org_id,
                        ImNotification.platform == platform,
                        ImNotification.status == "pending",
                    )
                    .order_by(col(ImNotification.created_at), col(ImNotification.id))
                    .limit(100)
                ).all()
            )
        return ids


def tracking_notice(
    db: Session,
    item: InboxItem,
    principal: Principal,
    *,
    borrow_assignment_id: UUID | None = None,
) -> str | None:
    from app.domains.capacity.demand import demand_row, represents_department
    from app.domains.capacity.tracking import (
        BORROW_TRACKING_URL,
        TRACKING_NOTICE,
        UNAVAILABILITY_NOTICE,
        list_active_borrow_assignments,
    )

    if (
        item.org_id != principal.org_id
        or item.kind != InboxKind.CAPACITY
        or not item.message.startswith(
            (f"{TRACKING_NOTICE}：", f"{UNAVAILABILITY_NOTICE}：")
        )
    ):
        return None
    body, _, url = item.message.rpartition("\n")
    if url == BORROW_TRACKING_URL:
        # Older notifications only have the display suffix, which cannot identify the source.
        if borrow_assignment_id is None:
            return None
        try:
            visible = list_active_borrow_assignments(
                principal, db, assignment_id=borrow_assignment_id
            )
        except HTTPException:
            return None
        if visible:
            return f"{body}\n{settings.FRONTEND_HOST.rstrip('/')}{url}"
        return None
    if not url.startswith("/capacity?tab=incoming&demand=") or not url.endswith(
        "&plans=1"
    ):
        return None
    try:
        demand_id = UUID(
            url.removeprefix("/capacity?tab=incoming&demand=").removesuffix("&plans=1")
        )
        demand = demand_row(principal, db, demand_id)
    except ValueError, HTTPException:
        return None
    if not represents_department(principal, demand.target_department_id):
        return None
    return f"{body}\n{settings.FRONTEND_HOST.rstrip('/')}{url}"


@contextmanager
def claim(
    engine: Engine, org_id: UUID, notification_id: UUID
) -> Generator[Pending | None]:
    from app.domains.capacity.tracking import BORROW_TRACKING_URL
    from app.domains.capacity.workload import (
        EXTENSION_REVIEW_NOTICE,
        WORKLOAD_REVIEW_NOTICE,
    )
    from app.domains.im.binding import principal_for

    with Session(engine) as db:
        row = db.exec(
            select(ImNotification)
            .where(
                ImNotification.org_id == org_id,
                ImNotification.id == notification_id,
                ImNotification.status == "pending",
            )
            .with_for_update(skip_locked=True)
        ).one_or_none()
        if row is None:
            yield None
            return
        item = db.exec(
            select(InboxItem).where(
                InboxItem.org_id == org_id, InboxItem.id == row.inbox_item_id
            )
        ).one()
        if item.done_at is not None:
            row.status, row.last_error_code = "failed", "inbox_done"
            db.add(row)
            db.commit()
            yield None
            return
        setting = db.get(ImSetting, (org_id, row.platform))
        bound = db.exec(
            select(ImBinding).where(
                ImBinding.org_id == org_id,
                ImBinding.employee_id == row.employee_id,
                ImBinding.platform == row.platform,
            )
        ).one_or_none()
        valid = bool(
            setting
            and setting.enabled
            and bound
            and bound.notify
            and bound.bot_identity == setting.bot_identity
            and bound.created_at <= row.created_at
        )
        custom_text = None
        if valid and bound:
            try:
                principal = principal_for(db, bound)
                custom_text = tracking_notice(
                    db, item, principal, borrow_assignment_id=row.borrow_assignment_id
                )
                if (
                    item.kind == InboxKind.CAPACITY
                    and item.message.endswith(f"\n{BORROW_TRACKING_URL}")
                    and custom_text is None
                ):
                    valid = False
                if item.kind == InboxKind.ADMIN_TODOS:
                    valid = (
                        principal.is_org_admin
                        and row.platform == "feishu"
                        and bool(item.admin_todo_count)
                    )
            except HTTPException:
                valid = False
        if not valid:
            row.status, row.last_error_code = "failed", "binding_inactive"
            db.add(row)
            db.commit()
            yield None
            return
        assert setting is not None and bound is not None
        notice = Pending(
            row.platform,
            setting.credentials,
            bound.external_chat_id,
            WORKER_ALERT_NOTIFICATION
            if item.kind == InboxKind.ADMIN_TODOS
            and item.message.startswith(WORKER_ALERT_PREFIX)
            else f"你有 {item.admin_todo_count} 条管理员待办，请在网页首页处理。"
            if item.kind == InboxKind.ADMIN_TODOS
            else item.message
            if item.kind == InboxKind.PROFILE_EDITED and row.platform == "feishu"
            else f"{REMINDERS[item.kind]}，在网页处理：{settings.FRONTEND_HOST.rstrip('/')}/capacity?tab=approvals"
            if item.kind == InboxKind.CAPACITY
            and item.message in (WORKLOAD_REVIEW_NOTICE, EXTENSION_REVIEW_NOTICE)
            else custom_text or REMINDERS[item.kind] + "，在网页或私聊我查看。",
        )
        yield notice
        if not notice.attempted:
            return
        row.attempts += 1
        row.last_error_code = notice.error
        if notice.error is None:
            row.status, row.sent_at = "sent", utc_now()
        elif row.attempts >= 3:
            row.status = "failed"
        # shortcut: A crash after sending can repeat a reminder; add platform idempotency when supported.
        db.add(row)
        db.commit()


async def send_pending(
    engine: Engine,
    adapters: dict[tuple[UUID, Platform], tuple[str, Adapter]],
) -> None:
    from app.domains.bot import delivery

    for org_id in await delivery._run(partial(organizations, engine)):
        for notification_id in await delivery._run(
            partial(pending_ids, engine, org_id)
        ):
            async with delivery.in_thread(
                claim(engine, org_id, notification_id)
            ) as notice:
                if notice is None:
                    continue
                connection = adapters.get((org_id, notice.platform))
                if not connection or connection[0] != notice.credentials:
                    continue
                notice.attempted = True
                try:
                    async with asyncio.timeout(10):
                        await connection[1].send_text(notice.chat_id, notice.text)
                except ImError as error:
                    notice.error = error.code
                except TimeoutError:
                    notice.error = "timeout"
