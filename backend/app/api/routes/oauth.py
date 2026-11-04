import secrets
from datetime import timedelta

import jwt
from fastapi import APIRouter, HTTPException, Response
from mcp.server.auth.provider import construct_redirect_uri
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import select

from app.api.deps import SessionDep
from app.authz.deps import OrgPrincipalDep
from app.core.config import settings
from app.core.security import ALGORITHM
from app.domains.access_token.auth import credential_principal
from app.domains.access_token.models import OAuthClient, OAuthGrant
from app.domains.access_token.oauth import digest
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


@router.post("/consent")
def consent(
    session: SessionDep,
    principal: OrgPrincipalDep,
    body: ConsentBody,
    response: Response,
) -> ConsentResult:
    try:
        claims = jwt.decode(
            body.request,
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
    if principal.employee_id is None:
        raise OAuthEmployeeRequired()
    assert principal.user_id is not None
    session.exec(
        select(Organization)
        .where(Organization.id == principal.org_id)
        .with_for_update()
    ).one()
    now = utc_now()
    credential_principal(
        session,
        principal.org_id,
        principal.user_id,
        principal.employee_id,
        now,
        client.client_metadata["client_name"],
    )
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
