"""Daily, tenant-scoped counts from the same overview used by the web console."""

import logging
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlmodel import Session, select

from app.domains.admin.overview import build_overview
from app.domains.im.binding import principal_for
from app.domains.im.models import ImBinding, ImSetting
from app.domains.inbox.models import InboxItem, InboxKind
from app.domains.inbox.service import add_item
from app.domains.org.models import Organization

logger = logging.getLogger(__name__)


def dispatch_admin_todos(db: Session, now: datetime) -> None:
    day = now.astimezone(UTC).date()
    org_ids = db.exec(select(Organization.id)).all()
    db.commit()
    for org_id in org_ids:
        try:
            setting = db.get(ImSetting, (org_id, "feishu"))
            if not setting or not setting.enabled:
                db.rollback()
                continue
            org = db.exec(
                select(Organization)
                .where(Organization.id == org_id)
                .with_for_update(skip_locked=True)
            ).one_or_none()
            if org is None:
                db.rollback()
                continue
            # shortcut: Administrators without employee identities use the web; add user bindings when supported.
            bindings = db.exec(
                select(ImBinding).where(
                    ImBinding.org_id == org_id,
                    ImBinding.platform == "feishu",
                    ImBinding.notify == True,  # noqa: E712
                    ImBinding.bot_identity == setting.bot_identity,
                )
            ).all()
            for binding in bindings:
                try:
                    p = principal_for(db, binding)
                except HTTPException:
                    continue
                if (
                    not p.is_org_admin
                    or db.exec(
                        select(InboxItem.id).where(
                            InboxItem.org_id == org_id,
                            InboxItem.recipient_user_id == p.user_id,
                            InboxItem.admin_todo_date == day,
                        )
                    ).first()
                ):
                    continue
                count = sum(todo.count for todo in build_overview(db, p).todos)
                if count:
                    add_item(
                        db,
                        InboxItem(
                            org_id=org_id,
                            recipient_user_id=p.user_id,
                            kind=InboxKind.ADMIN_TODOS,
                            admin_todo_date=day,
                            admin_todo_count=count,
                            message=f"你有 {count} 条管理员待办，请在网页首页处理。",
                        ),
                    )
            db.commit()
        except Exception as error:
            db.rollback()
            logger.error("org_id=%s error_type=%s", org_id, type(error).__name__)
