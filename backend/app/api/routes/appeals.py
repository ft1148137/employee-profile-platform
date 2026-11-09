from uuid import UUID

from fastapi import APIRouter

from app.api.deps import SessionDep
from app.authz.deps import OrgAdminDep, OrgPrincipalDep
from app.domains.review import appeal
from app.domains.review.appeal_schemas import (
    AppealInput,
    AppealPublic,
    AppealResolution,
    ResolveAppealInput,
)

router = APIRouter(prefix="/orgs/{org_id}/appeals", tags=["appeals"])


@router.post("")
def create_appeal(
    principal: OrgPrincipalDep, session: SessionDep, body: AppealInput
) -> AppealPublic:
    result = appeal.appeal_decision(
        principal, session, body.request_id, body.item_id, body.reason
    )
    session.commit()
    return result


@router.get("")
def list_disputes(
    principal: OrgAdminDep, session: SessionDep, employee_id: UUID | None = None
) -> list[AppealPublic]:
    return appeal.list_disputes(principal, session, employee_id=employee_id)


@router.get("/{appeal_id}")
def read_appeal(
    principal: OrgPrincipalDep, session: SessionDep, appeal_id: UUID
) -> AppealPublic:
    return appeal.read_appeal(principal, session, appeal_id)


@router.post("/{appeal_id}/resolve")
def resolve_appeal(
    principal: OrgAdminDep,
    session: SessionDep,
    appeal_id: UUID,
    body: ResolveAppealInput,
) -> AppealResolution:
    result = appeal.resolve_appeal(principal, session, appeal_id, body)
    session.commit()
    return result
