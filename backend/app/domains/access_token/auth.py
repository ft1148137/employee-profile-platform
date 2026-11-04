import hashlib

from fastapi import HTTPException
from sqlmodel import Session, select

from app.authz.deps import get_org_principal
from app.authz.principal import Principal
from app.domains.access_token.models import PersonalAccessToken
from app.domains.access_token.service import McpDisabled, get_setting
from app.domains.account.limiter import check_login_limit, record_login_attempt
from app.domains.base import utc_now
from app.domains.org.service import visible_employee
from app.models import User


class InvalidAccessToken(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            401, "令牌无效或已失效", headers={"WWW-Authenticate": "Bearer"}
        )


def token_principal(session: Session, plaintext: str, ip: str) -> Principal:
    check_login_limit(session, None, ip)
    token = session.exec(
        select(PersonalAccessToken).where(
            PersonalAccessToken.token_hash
            == hashlib.sha256(plaintext.encode()).hexdigest()
        )
    ).one_or_none()
    user = session.get(User, token.user_id) if token else None
    if (
        token is None
        or token.revoked_at is not None
        or token.expires_at <= utc_now()
        or user is None
        or not user.is_active
        or user.must_change_password
        or (user.password_changed_at and token.created_at <= user.password_changed_at)
    ):
        record_login_attempt(session, "mcp", ip, False)
        raise InvalidAccessToken()
    principal = get_org_principal(token.org_id, user, session)
    if principal.employee_id != token.employee_id:
        record_login_attempt(session, "mcp", ip, False)
        raise InvalidAccessToken()
    visible_employee(session, principal, token.employee_id)
    if not get_setting(session, principal).enabled:
        raise McpDisabled()
    token.last_used_at = utc_now()
    session.add(token)
    session.commit()
    return principal
