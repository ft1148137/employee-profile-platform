import asyncio
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import Engine
from sqlmodel import Session, col, select, update

from app.domains.base import utc_now
from app.domains.im.adapters.base import Adapter, ImError
from app.domains.im.models import ImBinding, ImNotification, ImSetting
from app.domains.im.schemas import Platform
from app.domains.inbox.models import InboxItem, InboxKind
from app.domains.org.models import Organization, OrgMembership

# Only fixed copy crosses the IM boundary; inbox messages may contain private data.
REMINDERS = {
    InboxKind.INITIAL_REVIEW: "你有新的画像请求待过目",
    InboxKind.SUBMITTED: "你的画像请求已提交",
    InboxKind.STATUS_CHANGED: "你的画像请求状态已更新",
    InboxKind.QUESTION: "你有待确认问题或定期提醒",
    InboxKind.DICTIONARY_CHANGED: "能力词典已更新",
    InboxKind.REVIEW_REQUESTED: "你有新的待审批请求",
    InboxKind.DECIDED: "你的画像请求有新的审批结果",
}


def enqueue(db: Session, item: InboxItem) -> None:
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
        db.add(
            ImNotification(
                org_id=item.org_id,
                inbox_item_id=item.id,
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


async def send_pending(
    engine: Engine,
    adapters: dict[tuple[UUID, Platform], tuple[str, Adapter]],
) -> None:
    from app.domains.im.binding import principal_for

    with Session(engine) as db:
        org_ids = db.exec(select(Organization.id)).all()
    for org_id in org_ids:
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
                        .order_by(
                            col(ImNotification.created_at), col(ImNotification.id)
                        )
                        .limit(100)
                    ).all()
                )
        for notification_id in ids:
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
                    continue
                item = db.exec(
                    select(InboxItem).where(
                        InboxItem.org_id == org_id, InboxItem.id == row.inbox_item_id
                    )
                ).one()
                if item.done_at is not None:
                    row.status, row.last_error_code = "failed", "inbox_done"
                    db.add(row)
                    db.commit()
                    continue
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
                if valid and bound:
                    try:
                        principal_for(db, bound)
                    except HTTPException:
                        valid = False
                if not valid:
                    row.status, row.last_error_code = "failed", "binding_inactive"
                    db.add(row)
                    db.commit()
                    continue
                connection = adapters.get((org_id, row.platform))
                if (
                    not connection
                    or not setting
                    or connection[0] != setting.credentials
                ):
                    continue
                assert bound is not None
                row.attempts += 1
                try:
                    async with asyncio.timeout(10):
                        await connection[1].send_text(
                            bound.external_chat_id,
                            REMINDERS[item.kind] + "，在网页或私聊我查看。",
                        )
                    row.status, row.sent_at, row.last_error_code = (
                        "sent",
                        utc_now(),
                        None,
                    )
                except ImError as error:
                    row.last_error_code = error.code
                except TimeoutError:
                    row.last_error_code = "timeout"
                if row.status == "pending" and row.attempts >= 3:
                    row.status = "failed"
                # shortcut: A crash after sending can repeat a reminder; add platform idempotency when supported.
                db.add(row)
                db.commit()
