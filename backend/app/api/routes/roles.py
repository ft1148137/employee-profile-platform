from uuid import UUID

from fastapi import APIRouter

from app.api.deps import SessionDep
from app.authz.deps import OrgPrincipalDep
from app.domains.account import service
from app.domains.account.schemas import GrantRole, RolePublic
from app.models import Message

router = APIRouter(prefix="/orgs/{org_id}/role-grants", tags=["roles"])


@router.get("", response_model=list[RolePublic])
def read_roles(session: SessionDep, principal: OrgPrincipalDep) -> list[RolePublic]:
    return service.list_roles(session, principal)


@router.post("", response_model=RolePublic)
def grant_role(
    body: GrantRole, session: SessionDep, principal: OrgPrincipalDep
) -> RolePublic:
    return service.grant_role(session, principal, body)


@router.delete("/{grant_id}", response_model=Message)
def revoke_role(
    grant_id: UUID, session: SessionDep, principal: OrgPrincipalDep
) -> Message:
    service.revoke_role(session, principal, grant_id)
    return Message(message="角色已撤销")
