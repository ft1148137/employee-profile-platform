from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.deps import SessionDep
from app.authz.deps import OrgPrincipalDep
from app.domains.planning import service
from app.domains.planning.schemas import CasePublic, OptionInput, OptionPublic

router = APIRouter(prefix="/orgs/{org_id}/planning", tags=["planning"])


def require_case_access(
    principal: OrgPrincipalDep, session: SessionDep, case_id: UUID
) -> service.CaseAccess:
    return service.authorized_case(principal, session, case_id)


@router.post("/demands/{demand_id}/cases")
def create_case(
    principal: OrgPrincipalDep, session: SessionDep, demand_id: UUID
) -> CasePublic:
    result = service.create_case(principal, session, demand_id)
    session.commit()
    return result


@router.get("/cases/{case_id}")
def get_case(
    principal: OrgPrincipalDep, session: SessionDep, case_id: UUID
) -> CasePublic:
    return service.get_case(principal, session, case_id)


@router.post("/cases/{case_id}/options")
def create_option(
    principal: OrgPrincipalDep,
    session: SessionDep,
    body: OptionInput,
    access: Annotated[service.CaseAccess, Depends(require_case_access)],
) -> OptionPublic:
    result = service.create_option(principal, session, access, body)
    session.commit()
    return result
