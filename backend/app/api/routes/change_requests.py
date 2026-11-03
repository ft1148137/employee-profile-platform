from typing import Literal
from uuid import UUID

from fastapi import APIRouter

from app.api.deps import SessionDep
from app.authz.deps import OrgPrincipalDep
from app.domains.change_request import service
from app.domains.change_request.schemas import ChangeRequestsPublic

router = APIRouter(
    prefix="/orgs/{org_id}/employees/{employee_id}", tags=["change_requests"]
)


@router.get("/change-requests", response_model=ChangeRequestsPublic)
def read_change_requests(
    employee_id: UUID,
    session: SessionDep,
    principal: OrgPrincipalDep,
    status: Literal["open", "all"] = "open",
) -> ChangeRequestsPublic:
    return service.list_requests(session, principal, employee_id, status)
