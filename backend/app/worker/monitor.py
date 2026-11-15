from datetime import datetime
from zoneinfo import ZoneInfo

from sqlmodel import Session, col, select

from app.domains.base import utc_now
from app.domains.inbox.models import InboxItem, InboxKind
from app.domains.inbox.service import add_item
from app.domains.org.models import Organization, OrgMembership, Role, RoleGrant
from app.domains.org.timezone import read_timezone
from app.models import User
from app.worker.health import HEARTBEAT_TIMEOUT, WORKER_ALERT_PREFIX, is_fresh
from app.worker.models import WorkerHeartbeat

PROCESS_STARTED_AT = utc_now()


def dispatch_worker_alerts(db: Session, now: datetime) -> None:
    heartbeat = db.get(WorkerHeartbeat, 1, populate_existing=True)
    if heartbeat is None and now - PROCESS_STARTED_AT < HEARTBEAT_TIMEOUT:
        db.rollback()
        return
    if is_fresh(heartbeat.beat_at if heartbeat else None, now):
        db.rollback()
        return
    org_ids = db.exec(select(Organization.id)).all()
    db.commit()
    for org_id in org_ids:
        # Serialize with other gateways; the inbox persists deduplication across restarts.
        org = db.exec(
            select(Organization)
            .where(Organization.id == org_id)
            .with_for_update(skip_locked=True)
        ).one_or_none()
        if org is None:
            db.rollback()
            continue
        heartbeat = db.get(WorkerHeartbeat, 1, populate_existing=True)
        beat_at = heartbeat.beat_at if heartbeat else None
        if is_fresh(beat_at, now):
            db.rollback()
            continue
        timezone = ZoneInfo(read_timezone(db, org_id).timezone)
        last_beat = (
            beat_at.astimezone(timezone).strftime("%Y-%m-%d %H:%M")
            if beat_at
            else "尚未收到"
        )
        message = (
            f"{WORKER_ALERT_PREFIX}自动起草、同步和定期提醒可能已停止。"
            f"最后心跳：{last_beat}。"
            "请在网页管理助手的「后台任务」状态卡查看处理步骤。"
        )
        admins = db.exec(
            select(OrgMembership.user_id)
            .join(
                RoleGrant,
                (col(RoleGrant.org_id) == col(OrgMembership.org_id))
                & (col(RoleGrant.membership_id) == col(OrgMembership.id)),
            )
            .join(User, col(User.id) == col(OrgMembership.user_id))
            .where(
                OrgMembership.org_id == org_id,
                RoleGrant.org_id == org_id,
                RoleGrant.role == Role.ORG_ADMIN,
                User.is_active == True,  # noqa: E712
            )
        ).all()
        for user_id in admins:
            if db.exec(
                select(InboxItem.id).where(
                    InboxItem.org_id == org_id,
                    InboxItem.recipient_user_id == user_id,
                    InboxItem.kind == InboxKind.ADMIN_TODOS,
                    InboxItem.message == message,
                )
            ).first():
                continue
            add_item(
                db,
                InboxItem(
                    org_id=org_id,
                    recipient_user_id=user_id,
                    kind=InboxKind.ADMIN_TODOS,
                    admin_todo_count=1,
                    message=message,
                ),
            )
        db.commit()
