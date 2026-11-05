from uuid import UUID

from fastapi import APIRouter

from app.api.deps import SessionDep
from app.authz.deps import OrgPrincipalDep
from app.domains.review import delegation

router = APIRouter(
    prefix="/orgs/{org_id}/me/review-delegations", tags=["review-delegations"]
)


@router.get("")
def read_delegations(
    session: SessionDep, principal: OrgPrincipalDep
) -> delegation.DelegationView:
    return delegation.delegation_view(session, principal)


@router.post("", status_code=201)
def create_delegation(
    session: SessionDep, principal: OrgPrincipalDep, body: delegation.DelegationInput
) -> delegation.DelegationView:
    delegation.create_delegation(session, principal, body)
    session.commit()
    return delegation.delegation_view(session, principal)


@router.delete("/{delegation_id}", status_code=204)
def revoke_delegation(
    session: SessionDep, principal: OrgPrincipalDep, delegation_id: UUID
) -> None:
    delegation.revoke_delegation(session, principal, delegation_id)
    session.commit()
