import hashlib
import secrets
from datetime import timedelta
from uuid import UUID

from fastapi import HTTPException
from sqlmodel import Session, col, select

from app.authz.principal import Principal
from app.domains.access_token.models import McpSetting, PersonalAccessToken
from app.domains.access_token.schemas import (
    McpSettingPublic,
    McpSettingUpdate,
    TokenCreated,
    TokenPublic,
)
from app.domains.audit.service import record_audit
from app.domains.base import utc_now
from app.domains.change_request.employee import employee_id
from app.domains.org.models import Organization
from app.domains.org.service import visible_employee


class McpDisabled(HTTPException):
    def __init__(self) -> None:
        super().__init__(403, "企业已关闭外部 AI 助手")


class TokenNotFound(HTTPException):
    def __init__(self) -> None:
        super().__init__(404, "令牌不存在")


def get_setting(session: Session, principal: Principal) -> McpSettingPublic:
    row = session.get(McpSetting, principal.org_id)
    return (
        McpSettingPublic.model_validate(row, from_attributes=True)
        if row
        else McpSettingPublic()
    )


def save_setting(
    session: Session,
    principal: Principal,
    body: McpSettingUpdate,
    *,
    commit: bool = True,
) -> McpSettingPublic:
    assert principal.user_id is not None
    session.exec(
        select(Organization)
        .where(Organization.id == principal.org_id)
        .with_for_update()
    ).one()
    row = session.get(McpSetting, principal.org_id)
    if row is None:
        row = McpSetting(org_id=principal.org_id, updated_by=principal.user_id)
    row.enabled, row.allowed_clients = (
        body.enabled,
        list(dict.fromkeys(body.allowed_clients)),
    )
    row.updated_by, row.updated_at = principal.user_id, utc_now()
    session.add(row)
    record_audit(
        session,
        org_id=principal.org_id,
        actor_type=principal.actor_type,
        actor_id=str(principal.user_id),
        action="mcp_setting.updated",
        target_type="mcp_setting",
        target_id=str(principal.org_id),
        details={
            "enabled": row.enabled,
            "allowed_clients": list(row.allowed_clients),
        },
    )
    session.flush()
    if commit:
        session.commit()
    return McpSettingPublic.model_validate(row, from_attributes=True)


def create_token(session: Session, principal: Principal, name: str) -> TokenCreated:
    assert principal.user_id is not None
    subject = employee_id(principal)
    visible_employee(session, principal, subject)
    # Serialize creation with enterprise switch changes.
    session.exec(
        select(Organization)
        .where(Organization.id == principal.org_id)
        .with_for_update()
    ).one()
    if not get_setting(session, principal).enabled:
        raise McpDisabled()
    plaintext = "epp_" + secrets.token_urlsafe(32)
    now = utc_now()
    row = PersonalAccessToken(
        org_id=principal.org_id,
        user_id=principal.user_id,
        employee_id=subject,
        name=name,
        token_hash=hashlib.sha256(plaintext.encode()).hexdigest(),
        prefix=plaintext[:12],
        created_at=now,
        expires_at=now + timedelta(days=90),
    )
    session.add(row)
    record_audit(
        session,
        org_id=principal.org_id,
        actor_type=principal.actor_type,
        actor_id=str(principal.user_id),
        action="access_token.created",
        target_type="personal_access_token",
        target_id=str(row.id),
        details={"prefix": row.prefix},
    )
    return TokenCreated(**TokenPublic.model_validate(row).model_dump(), token=plaintext)


def list_tokens(session: Session, principal: Principal) -> list[TokenPublic]:
    return [
        TokenPublic.model_validate(row)
        for row in session.exec(
            select(PersonalAccessToken)
            .where(
                PersonalAccessToken.org_id == principal.org_id,
                PersonalAccessToken.user_id == principal.user_id,
                PersonalAccessToken.client_kind == "pat",
            )
            .order_by(
                col(PersonalAccessToken.created_at).desc(),
                col(PersonalAccessToken.id).desc(),
            )
        ).all()
    ]


def revoke_token(session: Session, principal: Principal, token_id: UUID) -> TokenPublic:
    query = select(PersonalAccessToken).where(
        PersonalAccessToken.org_id == principal.org_id,
        PersonalAccessToken.user_id == principal.user_id,
        PersonalAccessToken.id == token_id,
    )
    row = session.exec(query).one_or_none()
    if row is None:
        raise TokenNotFound()
    if row.oauth_grant_id is not None:
        from app.domains.access_token.oauth import revoke_by_id

        # Match rotation's lock order: grant first, then its access tokens.
        revoke_by_id(session, principal.org_id, row.oauth_grant_id)
        return TokenPublic.model_validate(row).model_copy(
            update={"revoked_at": row.revoked_at or utc_now()}
        )
    row = session.exec(
        query.with_for_update().execution_options(populate_existing=True)
    ).one()
    if row.revoked_at is None:
        row.revoked_at = utc_now()
        session.add(row)
        record_audit(
            session,
            org_id=principal.org_id,
            actor_type=principal.actor_type,
            actor_id=str(principal.user_id),
            action="access_token.revoked",
            target_type="personal_access_token",
            target_id=str(row.id),
            details={"prefix": row.prefix},
        )
    return TokenPublic.model_validate(row)
