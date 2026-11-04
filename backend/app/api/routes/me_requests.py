from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.deps import SessionDep
from app.authz.deps import OrgPrincipalDep
from app.domains.capability.schemas import CapabilitiesPublic
from app.domains.change_request import employee
from app.domains.change_request.schemas import (
    ChangeRequestPublic,
    ChangeRequestsPublic,
    DraftInput,
    RequestPreview,
    SubmitInput,
)
from app.domains.evidence.models import EvidenceKind
from app.domains.evidence.schemas import EvidenceList
from app.domains.inbox.schemas import InboxList, InboxPublic
from app.domains.inbox.service import mark_read, my_inbox
from app.domains.profile.schemas import ProfileView

router = APIRouter(prefix="/orgs/{org_id}/me", tags=["employee_requests"])


@router.get("/profile")
def read_my_profile(principal: OrgPrincipalDep, session: SessionDep) -> ProfileView:
    return employee.my_profile(principal, session)


@router.get("/evidence")
def read_my_evidence(
    principal: OrgPrincipalDep,
    session: SessionDep,
    kind: EvidenceKind | None = None,
    include_inactive: bool = False,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> EvidenceList:
    return employee.my_evidence(principal, session, kind, include_inactive, skip, limit)


@router.get("/requests")
def read_my_requests(
    principal: OrgPrincipalDep,
    session: SessionDep,
    status: Literal["open", "all"] = "open",
) -> ChangeRequestsPublic:
    return employee.my_requests(principal, session, status)


@router.get("/capabilities")
def read_my_capabilities(
    principal: OrgPrincipalDep, session: SessionDep
) -> CapabilitiesPublic:
    return employee.capability_dictionary(principal, session)


@router.post("/requests", status_code=201)
def create_draft(
    principal: OrgPrincipalDep, session: SessionDep, body: DraftInput
) -> ChangeRequestPublic:
    result = employee.save_draft(principal, session, None, body.items, "web")
    session.commit()
    return result


@router.put("/requests/{request_id}/items")
def update_items(
    principal: OrgPrincipalDep, session: SessionDep, request_id: UUID, body: DraftInput
) -> ChangeRequestPublic:
    result = employee.save_draft(principal, session, request_id, body.items, "web")
    session.commit()
    return result


@router.get("/requests/{request_id}/preview")
def preview_request(
    principal: OrgPrincipalDep, session: SessionDep, request_id: UUID
) -> RequestPreview:
    return employee.preview(principal, session, request_id)


@router.post("/requests/{request_id}/submit")
def submit_request(
    principal: OrgPrincipalDep, session: SessionDep, request_id: UUID, body: SubmitInput
) -> ChangeRequestPublic:
    result = employee.submit(principal, session, request_id, body.revision, "web")
    session.commit()
    return result


@router.post("/requests/{request_id}/withdraw")
def withdraw_request(
    principal: OrgPrincipalDep, session: SessionDep, request_id: UUID
) -> ChangeRequestPublic:
    result = employee.withdraw(principal, session, request_id)
    session.commit()
    return result


@router.get("/inbox")
def read_inbox(
    principal: OrgPrincipalDep,
    session: SessionDep,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> InboxList:
    return my_inbox(principal, session, skip, limit)


@router.post("/inbox/{inbox_id}/read")
def read_inbox_item(
    principal: OrgPrincipalDep, session: SessionDep, inbox_id: UUID
) -> InboxPublic:
    result = mark_read(principal, session, inbox_id)
    session.commit()
    return result
