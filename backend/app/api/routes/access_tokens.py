from uuid import UUID

from fastapi import APIRouter, Response

from app.api.deps import SessionDep
from app.authz.deps import OrgAdminDep, OrgPrincipalDep
from app.domains.access_token import service
from app.domains.access_token.schemas import (
    McpSettingPublic,
    McpSettingUpdate,
    TokenCreate,
    TokenCreated,
    TokenPublic,
)

router = APIRouter(prefix="/orgs/{org_id}", tags=["access_tokens"])


@router.get("/mcp-setting")
def read_mcp_setting(
    session: SessionDep, principal: OrgPrincipalDep
) -> McpSettingPublic:
    return service.get_setting(session, principal)


@router.put("/mcp-setting")
def update_mcp_setting(
    session: SessionDep, principal: OrgAdminDep, body: McpSettingUpdate
) -> McpSettingPublic:
    result = service.save_setting(session, principal, body)
    session.commit()
    return result


@router.get("/me/access-tokens")
def read_access_tokens(
    session: SessionDep, principal: OrgPrincipalDep, response: Response
) -> list[TokenPublic]:
    response.headers["Cache-Control"] = "no-store"
    return service.list_tokens(session, principal)


@router.post("/me/access-tokens", status_code=201)
def create_access_token(
    session: SessionDep,
    principal: OrgPrincipalDep,
    body: TokenCreate,
    response: Response,
) -> TokenCreated:
    result = service.create_token(session, principal, body.name)
    session.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


@router.delete("/me/access-tokens/{token_id}")
def revoke_access_token(
    session: SessionDep, principal: OrgPrincipalDep, token_id: UUID
) -> TokenPublic:
    result = service.revoke_token(session, principal, token_id)
    session.commit()
    return result
