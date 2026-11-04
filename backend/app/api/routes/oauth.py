import secrets
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import jwt
from fastapi import APIRouter, HTTPException, Query, Response
from mcp.server.auth.provider import construct_redirect_uri
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, col, func, select

from app.api.deps import SessionDep
from app.authz.deps import OrgPrincipalDep
from app.authz.principal import Principal
from app.core.config import settings
from app.core.security import ALGORITHM
from app.domains.access_token.auth import credential_principal
from app.domains.access_token.models import OAuthClient, OAuthGrant, PersonalAccessToken
from app.domains.access_token.oauth import digest, revoke_by_id
from app.domains.access_token.service import McpDisabled, get_setting
from app.domains.audit.service import record_audit
from app.domains.base import utc_now
from app.domains.org.models import Organization
from app.mcp.oauth import resource_url

router = APIRouter(prefix="/oauth", tags=["oauth"])


class InvalidConsent(HTTPException):
    def __init__(self) -> None:
        super().__init__(400, "授权请求无效或已过期，请重新连接")


class OAuthEmployeeRequired(HTTPException):
    def __init__(self) -> None:
        super().__init__(403, "当前账号没有员工身份，不能授权")


class ConsentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: str = Field(max_length=8192, repr=False)
    approve: bool


class ConsentResult(BaseModel):
    redirect_to: str


class ClientNotAllowed(HTTPException):
    def __init__(self) -> None:
        super().__init__(403, "这个客户端不在企业允许列表中，请联系管理员")


class GrantNotFound(HTTPException):
    def __init__(self) -> None:
        super().__init__(404, "授权不存在")


class ConsentInfo(BaseModel):
    client_name: str
    org_name: str
    callback_host: str
    reason: str | None


class GrantPublic(BaseModel):
    id: UUID
    client_name: str
    created_at: datetime
    last_used_at: datetime | None


def validate_consent(
    session: Session, request: str
) -> tuple[dict[str, Any], OAuthClient]:
    try:
        claims = jwt.decode(
            request,
            settings.SECRET_KEY,
            algorithms=[ALGORITHM],
            audience="oauth-consent",
            options={
                "require": [
                    "exp",
                    "client_id",
                    "redirect_uri",
                    "code_challenge",
                    "resource",
                ]
            },
        )
    except jwt.InvalidTokenError:
        raise InvalidConsent() from None
    client = session.get(OAuthClient, claims["client_id"])
    if (
        client is None
        or claims["resource"] != resource_url()
        or claims["redirect_uri"] not in client.client_metadata["redirect_uris"]
    ):
        raise InvalidConsent()
    return claims, client


def check_eligibility(
    session: Session, principal: Principal, client: OAuthClient
) -> None:
    if principal.employee_id is None:
        raise OAuthEmployeeRequired()
    setting = get_setting(session, principal)
    if not setting.enabled:
        raise McpDisabled()
    if (
        setting.allowed_clients
        and client.client_metadata["client_name"] not in setting.allowed_clients
    ):
        raise ClientNotAllowed()
    assert principal.user_id is not None
    credential_principal(
        session,
        principal.org_id,
        principal.user_id,
        principal.employee_id,
        utc_now(),
        client.client_metadata["client_name"],
    )


@router.get("/consent")
def read_consent(
    session: SessionDep,
    principal: OrgPrincipalDep,
    response: Response,
    request: str = Query(max_length=8192),
) -> ConsentInfo:
    claims, client = validate_consent(session, request)
    reason = None
    try:
        check_eligibility(session, principal, client)
    except (OAuthEmployeeRequired, McpDisabled, ClientNotAllowed) as error:
        reason = str(error.detail)
    org = session.get(Organization, principal.org_id)
    assert org is not None
    response.headers["Cache-Control"] = "no-store"
    return ConsentInfo(
        client_name=client.client_metadata["client_name"],
        org_name=org.name,
        callback_host=urlsplit(claims["redirect_uri"]).hostname or "",
        reason=reason,
    )


@router.get("/grants")
def read_grants(
    session: SessionDep,
    principal: OrgPrincipalDep,
    response: Response,
) -> list[GrantPublic]:
    rows = session.exec(
        select(OAuthGrant, OAuthClient, func.max(PersonalAccessToken.last_used_at))
        .join(OAuthClient, col(OAuthClient.client_id) == col(OAuthGrant.client_id))
        .outerjoin(
            PersonalAccessToken,
            (col(PersonalAccessToken.org_id) == col(OAuthGrant.org_id))
            & (col(PersonalAccessToken.oauth_grant_id) == col(OAuthGrant.id)),
        )
        .where(
            OAuthGrant.org_id == principal.org_id,
            OAuthGrant.user_id == principal.user_id,
            col(OAuthGrant.revoked_at).is_(None),
        )
        .group_by(
            col(OAuthGrant.org_id), col(OAuthGrant.id), col(OAuthClient.client_id)
        )
        .order_by(col(OAuthGrant.created_at).desc(), col(OAuthGrant.id).desc())
    ).all()
    response.headers["Cache-Control"] = "no-store"
    return [
        GrantPublic(
            id=grant.id,
            client_name=client.client_metadata["client_name"],
            created_at=grant.created_at,
            last_used_at=last_used,
        )
        for grant, client, last_used in rows
    ]


@router.delete("/grants/{grant_id}", status_code=204)
def revoke_authorization(
    session: SessionDep,
    principal: OrgPrincipalDep,
    grant_id: UUID,
) -> None:
    grant = session.exec(
        select(OAuthGrant).where(
            OAuthGrant.org_id == principal.org_id,
            OAuthGrant.user_id == principal.user_id,
            OAuthGrant.id == grant_id,
        )
    ).one_or_none()
    if grant is None:
        raise GrantNotFound()
    revoke_by_id(session, principal.org_id, grant.id)
    session.commit()


@router.post("/consent")
def consent(
    session: SessionDep,
    principal: OrgPrincipalDep,
    body: ConsentBody,
    response: Response,
) -> ConsentResult:
    claims, client = validate_consent(session, body.request)
    session.exec(
        select(Organization)
        .where(Organization.id == principal.org_id)
        .with_for_update()
    ).one()
    check_eligibility(session, principal, client)
    assert principal.user_id is not None and principal.employee_id is not None
    now = utc_now()
    code = secrets.token_urlsafe(32) if body.approve else None
    if code:
        grant = OAuthGrant(
            org_id=principal.org_id,
            user_id=principal.user_id,
            employee_id=principal.employee_id,
            client_id=client.client_id,
            redirect_uri=claims["redirect_uri"],
            resource=claims["resource"],
            code_hash=digest(code),
            code_challenge=claims["code_challenge"],
            code_expires_at=now + timedelta(minutes=10),
        )
        session.add(grant)
    record_audit(
        session,
        org_id=principal.org_id,
        actor_type=principal.actor_type,
        actor_id=str(principal.user_id),
        action="oauth.allowed" if code else "oauth.denied",
        target_type="oauth_client",
        target_id=client.client_id,
        details={},
    )
    session.commit()
    response.headers["Cache-Control"] = "no-store"
    params = {"code": code} if code else {"error": "access_denied"}
    state = claims.get("state")
    if state is not None:
        params["state"] = state
    return ConsentResult(
        redirect_to=construct_redirect_uri(claims["redirect_uri"], **params)
    )
