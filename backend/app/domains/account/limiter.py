from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import or_
from sqlmodel import Session, col, delete, func, select

from app.core.config import settings
from app.domains.account.models import LoginAttempt
from app.models import User


class LoginLimited(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=429,
            detail=f"登录失败次数过多，请 {settings.LOGIN_LOCK_MINUTES} 分钟后再试，或联系企业管理员重置密码",
            headers={"Retry-After": str(settings.LOGIN_LOCK_MINUTES * 60)},
        )


def check_login_limit(session: Session, identifier: str, ip: str) -> None:
    # Serialize checks and recording across all API workers, without Redis.
    for key in (f"login-ip:{ip}", f"login-name:{identifier}"):
        session.exec(select(func.pg_advisory_xact_lock(func.hashtext(key)))).all()
    cutoff = datetime.now(UTC) - timedelta(minutes=settings.LOGIN_LOCK_MINUTES)
    failures = (
        select(func.count())
        .select_from(LoginAttempt)
        .outerjoin(
            User,
            or_(
                func.lower(User.username) == LoginAttempt.identifier_norm,
                func.lower(User.email) == LoginAttempt.identifier_norm,
            ),
        )
        .where(
            LoginAttempt.succeeded == False,  # noqa: E712
            LoginAttempt.created_at > cutoff,
            or_(
                col(User.last_login_at).is_(None),
                col(LoginAttempt.created_at) > col(User.last_login_at),
            ),
            or_(
                col(User.password_changed_at).is_(None),
                col(LoginAttempt.created_at) > col(User.password_changed_at),
            ),
        )
    )
    if (
        session.exec(failures.where(LoginAttempt.identifier_norm == identifier)).one()
        >= settings.LOGIN_MAX_FAILURES
        or session.exec(failures.where(LoginAttempt.ip == ip)).one()
        >= settings.LOGIN_MAX_FAILURES_PER_IP
    ):
        raise LoginLimited()


def record_login_attempt(
    session: Session, identifier: str, ip: str, succeeded: bool
) -> None:
    # Opportunistic cleanup needs no scheduler and shares the login transaction.
    session.exec(
        delete(LoginAttempt).where(
            col(LoginAttempt.created_at) < datetime.now(UTC) - timedelta(days=1)
        )
    )
    session.add(LoginAttempt(identifier_norm=identifier, ip=ip, succeeded=succeeded))
    session.commit()
