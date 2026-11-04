import hashlib
from datetime import datetime
from uuid import UUID

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


def credential_principal(
    session: Session,
    org_id: UUID,
    user_id: UUID,
    employee_id: UUID,
    created_at: datetime,
    client_name: str | None = None,
) -> Principal:
    user = session.get(User, user_id)
    if (
        user is None
        or not user.is_active
        or user.must_change_password
        or (user.password_changed_at and created_at <= user.password_changed_at)
    ):
        raise InvalidAccessToken()
    principal = get_org_principal(org_id, user, session)
    if principal.employee_id != employee_id:
        raise InvalidAccessToken()
    visible_employee(session, principal, employee_id)
    setting = get_setting(session, principal)
    if not setting.enabled:
        raise McpDisabled()
    if (
        client_name is not None
        and setting.allowed_clients
        and client_name not in setting.allowed_clients
    ):
        raise InvalidAccessToken()
    return principal


def token_principal(session: Session, plaintext: str, ip: str) -> Principal:
    check_login_limit(session, None, ip)
    token = session.exec(
        select(PersonalAccessToken).where(
            PersonalAccessToken.token_hash
            == hashlib.sha256(plaintext.encode()).hexdigest()
        )
    ).one_or_none()
    try:
        if (
            token is None
            or token.revoked_at is not None
            or token.expires_at <= utc_now()
        ):
            raise InvalidAccessToken()
        principal = credential_principal(
            session,
            token.org_id,
            token.user_id,
            token.employee_id,
            token.created_at,
            token.name if token.client_kind == "oauth" else None,
        )
    except InvalidAccessToken:
        record_login_attempt(session, "mcp", ip, False)
        raise
    token.last_used_at = utc_now()
    session.add(token)
    session.commit()
    return principal
