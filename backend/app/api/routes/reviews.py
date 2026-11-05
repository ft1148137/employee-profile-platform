from uuid import UUID

from fastapi import APIRouter

from app.api.deps import SessionDep
from app.authz.deps import OrgPrincipalDep
from app.domains.review import service
from app.domains.review.schemas import (
    DecideInput,
    DecisionResult,
    PendingReviews,
    ReviewDetail,
)

router = APIRouter(prefix="/orgs/{org_id}/reviews", tags=["reviews"])


@router.get("")
def list_pending_reviews(
    principal: OrgPrincipalDep, session: SessionDep
) -> PendingReviews:
    return service.list_pending_reviews(principal, session)


@router.get("/{request_id}")
def get_review(
    principal: OrgPrincipalDep, session: SessionDep, request_id: UUID
) -> ReviewDetail:
    return service.get_review(principal, session, request_id)


@router.post("/{request_id}/decisions")
def decide(
    principal: OrgPrincipalDep, session: SessionDep, request_id: UUID, body: DecideInput
) -> DecisionResult:
    result = service.decide(
        principal, session, request_id, body.revision, body.decisions, body.comment
    )
    session.commit()
    return result
