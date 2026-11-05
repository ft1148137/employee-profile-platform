import hashlib
import hmac
import secrets
from datetime import timedelta
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, or_
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import Session, col, delete, select

from app.authz.principal import Principal
from app.core.config import settings
from app.domains.access_token.auth import account_principal
from app.domains.account.limiter import check_login_limit, record_login_attempt
from app.domains.audit.service import record_audit
from app.domains.base import utc_now
from app.domains.im.adapters.base import Event
from app.domains.im.models import ImAction, ImBindCode, ImBinding, ImEvent, ImSetting
from app.domains.im.notifications import cancel_pending
from app.domains.im.schemas import BindingCode, BindingPublic, Platform
from app.domains.org.models import (
    Employee,
    EmploymentStatus,
    Organization,
    OrgMembership,
)
from app.domains.org.service import EmployeeNotFound, visible_employee

GUIDE = "请在网页的个人设置中生成绑定码，再私聊发送「绑定 123456」。"


def _lock(db: Session, org_id: UUID) -> None:
    db.exec(
        select(Organization).where(Organization.id == org_id).with_for_update()
    ).one()


def _audit(db: Session, p: Principal, action: str, target: str) -> None:
    record_audit(
        db,
        org_id=p.org_id,
        actor_type=p.actor_type,
        actor_id=str(p.user_id),
        action=action,
        target_type="im_binding",
        target_id=target,
        details={},
    )


def _hash(code: str) -> str:
    # A keyed hash protects the small six-digit space from offline enumeration.
    return hmac.new(
        settings.SECRET_KEY.encode(), code.encode(), hashlib.sha256
    ).hexdigest()


def create_code(db: Session, p: Principal) -> BindingCode:
    if p.employee_id is None:
        raise EmployeeNotFound()
    visible_employee(db, p, p.employee_id)
    _lock(db, p.org_id)
    db.exec(
        delete(ImBindCode).where(
            col(ImBindCode.org_id) == p.org_id,
            col(ImBindCode.employee_id) == p.employee_id,
        )
    )
    while True:
        code = f"{secrets.randbelow(1_000_000):06d}"
        if (
            db.exec(
                select(ImBindCode.id).where(
                    col(ImBindCode.org_id) == p.org_id,
                    ImBindCode.code_hash == _hash(code),
                )
            ).first()
            is None
        ):
            break
    row = ImBindCode(
        org_id=p.org_id,
        employee_id=p.employee_id,
        code_hash=_hash(code),
        expires_at=utc_now() + timedelta(minutes=10),
    )
    db.add(row)
    _audit(db, p, "im_binding.code_created", str(p.employee_id))
    db.commit()
    return BindingCode(code=code, expires_at=row.expires_at)


def principal_for(db: Session, row: ImBinding | ImBindCode) -> Principal:
    user_id = db.exec(
        select(OrgMembership.user_id).where(
            OrgMembership.org_id == row.org_id,
            OrgMembership.employee_id == row.employee_id,
        )
    ).one_or_none()
    if user_id is None:
        raise EmployeeNotFound()
    return account_principal(db, row.org_id, user_id, row.employee_id, row.created_at)


def current_binding(
    db: Session,
    org_id: UUID,
    platform: Platform,
    external_user_id: str,
    chat_id: str,
    bot_identity: str,
) -> Principal | None:
    setting = db.get(ImSetting, (org_id, platform))
    if not setting or not setting.enabled:
        return None
    row = db.exec(
        select(ImBinding).where(
            col(ImBinding.org_id) == org_id,
            col(ImBinding.platform) == platform,
            col(ImBinding.external_user_id) == external_user_id,
            ImBinding.external_chat_id == chat_id,
            ImBinding.bot_identity == bot_identity,
        )
    ).one_or_none()
    if row is None:
        return None
    try:
        return principal_for(db, row)
    except HTTPException:
        return None


def bind(
    db: Session,
    org_id: UUID,
    platform: Platform,
    external_user_id: str,
    chat_id: str,
    bot_identity: str,
    code: str,
) -> bool:
    identifier = f"im:{org_id}:{platform}:{external_user_id}"
    ip = f"im:{org_id}:{platform}"
    check_login_limit(db, identifier, ip)
    _lock(db, org_id)
    setting = db.get(ImSetting, (org_id, platform), populate_existing=True)
    if not setting or not setting.enabled or setting.bot_identity != bot_identity:
        return False
    row = db.exec(
        select(ImBindCode).where(
            col(ImBindCode.org_id) == org_id,
            ImBindCode.code_hash == _hash(code),
            col(ImBindCode.used_at).is_(None),
            ImBindCode.expires_at > utc_now(),
        )
    ).one_or_none()
    p = None
    if row is not None:
        try:
            p = principal_for(db, row)
        except HTTPException:
            pass
    if row is None or p is None:
        record_login_attempt(db, identifier, ip, False)
        return False
    existing = db.exec(
        select(ImBinding).where(
            col(ImBinding.org_id) == org_id,
            col(ImBinding.platform) == platform,
            or_(
                col(ImBinding.employee_id) == row.employee_id,
                col(ImBinding.external_user_id) == external_user_id,
            ),
        )
    ).all()
    if any(
        b.bot_identity == bot_identity
        and (b.employee_id != row.employee_id or b.external_user_id != external_user_id)
        for b in existing
    ):
        record_login_attempt(db, identifier, ip, False)
        return False
    for old in existing:
        db.delete(old)
    db.flush()
    row.used_at = utc_now()
    db.add(row)
    db.add(
        ImBinding(
            org_id=org_id,
            employee_id=row.employee_id,
            platform=platform,
            external_user_id=external_user_id,
            external_chat_id=chat_id,
            bot_identity=bot_identity,
        )
    )
    _audit(db, p, "im_binding.bound", platform)
    record_login_attempt(db, identifier, ip, True)
    return True


def wants_email(db: Session, event: Event, identity: str) -> bool:
    setting = db.get(ImSetting, (event.org_id, event.platform))
    return bool(
        setting
        and setting.enabled
        and setting.email_auto_bind
        and setting.bot_identity == identity
        and db.exec(
            select(ImBinding.id).where(
                ImBinding.org_id == event.org_id,
                ImBinding.platform == event.platform,
                ImBinding.external_user_id == event.external_user_id,
            )
        ).first()
        is None
    )


def bind_email(
    db: Session, event: Event, identity: str, credentials: str, email: str
) -> bool:
    _lock(db, event.org_id)
    setting = db.get(ImSetting, (event.org_id, event.platform), populate_existing=True)
    if (
        not event.private
        or not setting
        or setting.credentials != credentials
        or not wants_email(db, event, identity)
    ):
        return False
    employees = db.exec(
        select(Employee).where(
            Employee.org_id == event.org_id,
            func.lower(Employee.work_email) == email.strip().lower(),
        )
    ).all()
    if len(employees) != 1:
        return False
    employee = employees[0]
    if employee.employment_status != EmploymentStatus.ACTIVE:
        return False
    if db.exec(
        select(ImBinding.id).where(
            ImBinding.org_id == event.org_id,
            ImBinding.platform == event.platform,
            ImBinding.employee_id == employee.id,
        )
    ).first():
        return False
    row = ImBinding(
        org_id=event.org_id,
        employee_id=employee.id,
        platform=event.platform,
        external_user_id=event.external_user_id,
        external_chat_id=event.external_chat_id,
        bot_identity=identity,
        bound_via="email",
    )
    try:
        p = principal_for(db, row)
    except HTTPException:
        return False
    added = (
        db.connection()
        .execute(
            insert(ImEvent)
            .values(
                org_id=event.org_id,
                platform=event.platform,
                event_id=f"{identity}:{event.event_id}",
            )
            .on_conflict_do_nothing()
            .returning(col(ImEvent.event_id))
        )
        .first()
    )
    if added is None:
        return False
    db.add(row)
    _audit(db, p, "im_binding.bound", event.platform)
    db.commit()
    return True


def list_bindings(db: Session, p: Principal) -> list[BindingPublic]:
    result = []
    for row in db.exec(
        select(ImBinding).where(
            col(ImBinding.org_id) == p.org_id,
            col(ImBinding.employee_id) == p.employee_id,
        )
    ).all():
        setting = db.get(ImSetting, (p.org_id, row.platform))
        active = bool(
            setting and setting.enabled and setting.bot_identity == row.bot_identity
        )
        try:
            principal_for(db, row)
        except HTTPException:
            active = False
        result.append(
            BindingPublic(
                platform=row.platform,
                external_user_id=row.external_user_id,
                created_at=row.created_at,
                notify=row.notify,
                active=active,
            )
        )
    return result


def set_notify(db: Session, p: Principal, platform: Platform, notify: bool) -> None:
    if p.employee_id is None:
        raise EmployeeNotFound()
    _lock(db, p.org_id)
    row = db.exec(
        select(ImBinding).where(
            ImBinding.org_id == p.org_id,
            ImBinding.employee_id == p.employee_id,
            ImBinding.platform == platform,
        )
    ).one_or_none()
    if row is None:
        raise EmployeeNotFound()
    row.notify = notify
    db.add(row)
    if not notify:
        cancel_pending(db, p.org_id, platform, p.employee_id)
    _audit(db, p, "im_binding.notify_updated", platform)
    db.commit()


def unbind(db: Session, p: Principal, platform: Platform) -> None:
    _lock(db, p.org_id)
    if p.employee_id is not None:
        cancel_pending(db, p.org_id, platform, p.employee_id)
    db.exec(
        delete(ImBinding).where(
            col(ImBinding.org_id) == p.org_id,
            col(ImBinding.employee_id) == p.employee_id,
            col(ImBinding.platform) == platform,
        )
    )
    db.exec(
        delete(ImBindCode).where(
            col(ImBindCode.org_id) == p.org_id,
            col(ImBindCode.employee_id) == p.employee_id,
        )
    )
    _audit(db, p, "im_binding.unbound", platform)
    db.commit()


def cleanup_events(db: Session) -> None:
    for org_id in db.exec(select(Organization.id)).all():
        db.exec(
            delete(ImAction).where(
                col(ImAction.org_id) == org_id, col(ImAction.expires_at) <= utc_now()
            )
        )
        db.exec(
            delete(ImEvent).where(
                col(ImEvent.org_id) == org_id,
                col(ImEvent.received_at) < utc_now() - timedelta(days=7),
            )
        )
    db.commit()
