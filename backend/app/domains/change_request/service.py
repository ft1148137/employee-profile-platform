from uuid import UUID

from sqlalchemy import case, literal, union_all
from sqlalchemy.orm import aliased
from sqlmodel import Session, col, select

from app.domains.base import utc_now
from app.domains.change_request.models import ProfileChangeRequest, RequestStatus
from app.domains.profile.models import (
    AssessmentStatus,
    ProfileClaim,
    ProgressReviewStatus,
)

ALLOWED_TRANSITIONS: dict[RequestStatus, set[RequestStatus]] = {
    RequestStatus.DRAFT: {RequestStatus.PENDING_REVIEW, RequestStatus.WITHDRAWN},
    RequestStatus.PENDING_OWNER: {RequestStatus.DRAFT, RequestStatus.PENDING_REVIEW},
    RequestStatus.PENDING_REVIEW: {
        RequestStatus.APPROVED,
        RequestStatus.CHANGES_REQUESTED,
        RequestStatus.REJECTED,
        RequestStatus.WITHDRAWN,
    },
}


class InvalidTransition(ValueError):
    """The request cannot leave its current state for the requested state."""


def transition(request: ProfileChangeRequest, to: RequestStatus) -> None:
    if to not in ALLOWED_TRANSITIONS.get(request.status, set()):
        raise InvalidTransition(f"Cannot transition {request.status} to {to}")
    request.status = to
    request.updated_at = utc_now()


def review_statuses(
    session: Session, org_id: UUID, employee_ids: list[UUID], published_ids: set[UUID]
) -> dict[UUID, ProgressReviewStatus]:
    statuses = {
        employee_id: ProgressReviewStatus.PUBLISHED
        if employee_id in published_ids
        else ProgressReviewStatus.NONE
        for employee_id in employee_ids
    }
    successor = aliased(ProfileClaim)
    current = (
        select(ProfileClaim.subject_employee_id, ProfileClaim.assessment_status)
        .where(
            ProfileClaim.org_id == org_id,
            col(ProfileClaim.subject_employee_id).in_(employee_ids),
            ~select(successor.id)
            .where(
                successor.org_id == org_id,
                successor.supersedes_claim_id == ProfileClaim.id,
                successor.capability_definition_id
                == ProfileClaim.capability_definition_id,
            )
            .exists(),
        )
        .distinct(
            col(ProfileClaim.subject_employee_id),
            col(ProfileClaim.capability_definition_id),
        )
        .order_by(
            col(ProfileClaim.subject_employee_id),
            col(ProfileClaim.capability_definition_id),
            col(ProfileClaim.created_at).desc(),
            col(ProfileClaim.id).desc(),
        )
    ).subquery()
    disputed = select(
        current.c.subject_employee_id, literal("disputed").label("review_status")
    ).where(current.c.assessment_status == AssessmentStatus.DISPUTED)
    pending = select(
        ProfileChangeRequest.subject_employee_id,
        case(
            (
                col(ProfileChangeRequest.status) == RequestStatus.PENDING_OWNER,
                "pending_employee",
            ),
            else_="pending_manager",
        ),
    ).where(
        ProfileChangeRequest.org_id == org_id,
        col(ProfileChangeRequest.subject_employee_id).in_(employee_ids),
        col(ProfileChangeRequest.status).in_(
            [RequestStatus.PENDING_OWNER, RequestStatus.PENDING_REVIEW]
        ),
    )
    priority = {"disputed": 1, "pending_manager": 2, "pending_employee": 3}
    combined = union_all(disputed, pending).subquery()
    for employee_id, value in session.exec(
        select(combined.c.subject_employee_id, combined.c.review_status)
    ).all():
        if priority[value] > priority.get(statuses[employee_id], 0):
            statuses[employee_id] = ProgressReviewStatus(value)
    return statuses
