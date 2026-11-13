from typing import Literal
from uuid import UUID

from sqlalchemy import and_, case, literal, or_, union_all
from sqlalchemy.orm import aliased
from sqlmodel import Session, col, select

from app.authz.policies import can_view_evidence_content
from app.authz.principal import Principal
from app.domains.base import utc_now
from app.domains.capability.models import CapabilityDefinition
from app.domains.capability.schemas import CapabilityPublic
from app.domains.change_request.models import (
    ChangeItem,
    ChangeItemEvidence,
    ProfileChangeRequest,
    RequestOrigin,
    RequestStatus,
)
from app.domains.change_request.schemas import (
    ChangeItemPublic,
    ChangeRequestPublic,
    ChangeRequestsPublic,
    RequestReviewer,
)
from app.domains.evidence.models import Visibility
from app.domains.profile.models import (
    AssessmentStatus,
    ProfileClaim,
    ProgressReviewStatus,
)
from app.domains.profile.schemas import ClaimEvidencePublic
from app.domains.review.models import AppealStatus, ReviewAppeal


def _reviewers(
    session: Session, org_id: UUID, employee_id: UUID
) -> list[RequestReviewer]:
    from app.authz.deps import get_org_principal
    from app.domains.org.models import Employee, OrgMembership, ReportingLine
    from app.domains.review.routing import active_delegations, reviewers_for
    from app.models import User

    reviewers = reviewers_for(session, org_id, employee_id)
    delegates = active_delegations(session, org_id, reviewers)
    delegated = {row.delegate_user_id for row in delegates}
    line = session.exec(
        select(ReportingLine).where(
            ReportingLine.org_id == org_id,
            ReportingLine.employee_id == employee_id,
            col(ReportingLine.valid_to).is_(None),
        )
    ).one_or_none()
    subject = session.exec(
        select(Employee).where(Employee.org_id == org_id, Employee.id == employee_id)
    ).one()
    people = session.exec(
        select(OrgMembership, User, Employee)
        .join(User, col(User.id) == OrgMembership.user_id)
        .outerjoin(
            Employee,
            (col(Employee.org_id) == OrgMembership.org_id)
            & (col(Employee.id) == OrgMembership.employee_id),
        )
        .where(
            OrgMembership.org_id == org_id,
            col(OrgMembership.user_id).in_(set(reviewers) | delegated),
        )
    ).all()
    by_user = {user.id: (member, user, employee) for member, user, employee in people}
    result = []
    for user_id in reviewers + sorted(delegated - set(reviewers)):
        member, user, employee = by_user[user_id]
        if member.employee_id == employee_id:
            continue
        role: Literal["直属上级", "部门经理", "代理审批", "企业管理员"]
        if user_id not in reviewers:
            role = "代理审批"
        elif (
            subject.department_id
            in get_org_principal(org_id, user, session).managed_department_ids
        ):
            role = (
                "直属上级"
                if line and member.employee_id == line.manager_employee_id
                else "部门经理"
            )
        else:
            role = "企业管理员"
        result.append(
            RequestReviewer(
                name=employee.display_name
                if employee
                else user.full_name or "企业管理员",
                role=role,
            )
        )
    return result


def list_requests(
    session: Session,
    principal: Principal,
    employee_id: UUID,
    status: Literal["open", "all"],
    request_id: UUID | None = None,
) -> ChangeRequestsPublic:
    # Employee summaries also derive their review status from this module.
    from app.authz.review import review_access
    from app.domains.evidence.service import evidence_access, restricted_title
    from app.domains.org.service import visible_employee

    employee = visible_employee(session, principal, employee_id)
    query = select(ProfileChangeRequest).where(
        ProfileChangeRequest.org_id == principal.org_id,
        ProfileChangeRequest.subject_employee_id == employee_id,
    )
    if principal.employee_id != employee_id:
        query = query.where(ProfileChangeRequest.status != RequestStatus.DRAFT)
    if request_id is not None:
        query = query.where(ProfileChangeRequest.id == request_id)
    if status == "open":
        query = query.where(
            col(ProfileChangeRequest.status).in_(
                [
                    RequestStatus.DRAFT,
                    RequestStatus.PENDING_OWNER,
                    RequestStatus.PENDING_REVIEW,
                ]
            )
        )
    request_rows = session.exec(
        query.order_by(
            col(ProfileChangeRequest.created_at).desc(),
            col(ProfileChangeRequest.id),
        )
    ).all()
    requests = {row.id: ChangeRequestPublic.model_validate(row) for row in request_rows}
    if principal.employee_id == employee_id and requests:
        reviewers = _reviewers(session, principal.org_id, employee_id)
        for request in requests.values():
            if request.status in (
                RequestStatus.DRAFT,
                RequestStatus.PENDING_OWNER,
                RequestStatus.PENDING_REVIEW,
            ):
                request.reviewers = reviewers
    reviewable = {
        row.id for row in request_rows if review_access(session, principal, row)
    }
    items: dict[UUID, ChangeItemPublic] = {}
    for item, capability in session.exec(
        select(ChangeItem, CapabilityDefinition)
        .join(
            CapabilityDefinition,
            (col(CapabilityDefinition.id) == ChangeItem.capability_definition_id)
            & (col(CapabilityDefinition.org_id) == ChangeItem.org_id),
        )
        .where(
            ChangeItem.org_id == principal.org_id,
            col(ChangeItem.request_id).in_(requests),
        )
        .order_by(col(ChangeItem.position))
    ).all():
        public = ChangeItemPublic.model_validate(
            item, update={"capability": CapabilityPublic.model_validate(capability)}
        )
        if item.request_id not in reviewable and not can_view_evidence_content(
            principal, subject_employee=employee, visibility=Visibility.RESTRICTED
        ):
            public.owner_statement = public.objection = None
            public.decision_reason = None
        items[item.id] = public
        requests[item.request_id].items.append(public)
    for appeal in session.exec(
        select(ReviewAppeal)
        .where(
            ReviewAppeal.org_id == principal.org_id,
            col(ReviewAppeal.item_id).in_(items),
        )
        .order_by(col(ReviewAppeal.created_at), col(ReviewAppeal.id))
    ).all():
        items[appeal.item_id].appeal_id = appeal.id
    links = session.exec(
        select(ChangeItemEvidence)
        .where(
            ChangeItemEvidence.org_id == principal.org_id,
            col(ChangeItemEvidence.item_id).in_(items),
        )
        .order_by(col(ChangeItemEvidence.evidence_id))
    ).all()
    access = evidence_access(session, principal, {link.evidence_id for link in links})
    for link in links:
        record = access.get(link.evidence_id)
        if record is None:
            continue
        evidence, readable = record
        items[link.item_id].evidence.append(
            ClaimEvidencePublic(
                evidence_id=evidence.id,
                kind=evidence.kind,
                title=evidence.title if readable else restricted_title(evidence.kind),
                occurred_at=evidence.occurred_at,
                relation=link.relation,
                redacted=not readable,
            )
        )
    return ChangeRequestsPublic(data=list(requests.values()), count=len(requests))


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
    request.revision += 1


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
                col(ProfileChangeRequest.status).in_(
                    [RequestStatus.PENDING_OWNER, RequestStatus.DRAFT]
                ),
                "pending_employee",
            ),
            else_="pending_manager",
        ),
    ).where(
        ProfileChangeRequest.org_id == org_id,
        col(ProfileChangeRequest.subject_employee_id).in_(employee_ids),
        or_(
            col(ProfileChangeRequest.status).in_(
                [RequestStatus.PENDING_OWNER, RequestStatus.PENDING_REVIEW]
            ),
            and_(
                col(ProfileChangeRequest.status) == RequestStatus.DRAFT,
                col(ProfileChangeRequest.origin) == RequestOrigin.INITIAL_SYNC,
            ),
        ),
    )
    priority = {
        "disputed": 1,
        "pending_manager": 2,
        "pending_employee": 3,
        "appealed": 4,
    }
    appeals = select(ReviewAppeal.employee_id, literal("appealed")).where(
        ReviewAppeal.org_id == org_id,
        col(ReviewAppeal.employee_id).in_(employee_ids),
        ReviewAppeal.status == AppealStatus.OPEN,
    )
    combined = union_all(disputed, pending, appeals).subquery()
    assigned: dict[UUID, int] = {}
    for employee_id, value in session.exec(
        select(combined.c.subject_employee_id, combined.c.review_status)
    ).all():
        if priority[value] > assigned.get(employee_id, 0):
            assigned[employee_id] = priority[value]
            statuses[employee_id] = (
                ProgressReviewStatus.DISPUTED
                if value == "appealed"
                else ProgressReviewStatus(value)
            )
    return statuses
