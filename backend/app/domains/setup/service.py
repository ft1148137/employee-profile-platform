import hashlib
import secrets
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, func, select

from app import crud
from app.core.config import settings
from app.core.security import create_access_token, get_password_hash
from app.domains.audit.models import ActorKind
from app.domains.audit.service import record_audit
from app.domains.base import utc_now
from app.domains.org.models import Organization, OrgMembership, Role, RoleGrant
from app.domains.setup.models import SetupPurpose, SetupToken
from app.domains.setup.schemas import InitializeInput, InitializeResult
from app.models import User


class AlreadyInitialized(HTTPException):
    def __init__(self) -> None:
        super().__init__(409, "企业已经初始化，请用管理员账号登录")


class InvalidSetupToken(HTTPException):
    def __init__(self) -> None:
        super().__init__(400, "初始化链接无效或已过期，请联系部署人员重新获取")


class UsernameTaken(HTTPException):
    def __init__(self) -> None:
        super().__init__(409, "这个登录名已经被使用，请换一个")


def is_initialized(session: Session) -> bool:
    return session.exec(select(Organization.id).limit(1)).first() is not None


def lock_setup(session: Session) -> None:
    # Platform-wide lock also serializes empty-database checks and token rotation.
    session.exec(select(func.pg_advisory_xact_lock(281107)))


def issue_token(session: Session, purpose: SetupPurpose) -> str:
    lock_setup(session)
    if purpose == SetupPurpose.INITIAL_SETUP and is_initialized(session):
        raise AlreadyInitialized()
    now = utc_now()
    for old in session.exec(
        select(SetupToken).where(
            SetupToken.purpose == purpose,
            col(SetupToken.used_at).is_(None),
            SetupToken.expires_at > now,
        )
    ).all():
        old.expires_at = now
        session.add(old)
    token = secrets.token_urlsafe(32)
    session.add(
        SetupToken(
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            purpose=purpose,
            expires_at=now + timedelta(hours=72),
        )
    )
    session.flush()
    return token


def initialize(session: Session, data: InitializeInput) -> InitializeResult:
    lock_setup(session)
    token = session.exec(
        select(SetupToken)
        .where(SetupToken.token_hash == hashlib.sha256(data.token.encode()).hexdigest())
        .with_for_update()
    ).first()
    now = utc_now()
    if token is None or token.used_at is not None or token.expires_at <= now:
        raise InvalidSetupToken()
    if token.purpose == SetupPurpose.INITIAL_SETUP and is_initialized(session):
        raise AlreadyInitialized()
    if crud.get_user_by_username(session=session, username=data.admin_username):
        raise UsernameTaken()
    while True:
        org = Organization(slug=f"org-{secrets.token_hex(8)}", name=data.org_name)
        try:
            with session.begin_nested():
                session.add(org)
                session.flush()
            break
        except IntegrityError as exc:
            if (
                getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
                != "organization_slug_key"
            ):
                raise
    user = User(
        username=data.admin_username,
        full_name=data.admin_full_name,
        hashed_password=get_password_hash(data.admin_password),
        must_change_password=False,
        password_changed_at=now,
        is_superuser=False,
    )
    try:
        with session.begin_nested():
            session.add(user)
            session.flush()
    except IntegrityError as exc:
        if (
            getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            == "ix_user_username_lower"
        ):
            raise UsernameTaken() from exc
        raise
    membership = OrgMembership(org_id=org.id, user_id=user.id)
    session.add(membership)
    session.flush()
    session.add(
        RoleGrant(org_id=org.id, membership_id=membership.id, role=Role.ORG_ADMIN)
    )
    token.used_at = now
    session.add(token)
    record_audit(
        session,
        org_id=org.id,
        actor_type=ActorKind.USER,
        actor_id=str(user.id),
        action="setup.initialized",
        target_type="organization",
        target_id=str(org.id),
        details={"purpose": token.purpose.value},
    )
    return InitializeResult(
        access_token=create_access_token(
            user.id,
            timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
            password_changed_at=user.password_changed_at,
        ),
        org_id=org.id,
    )
