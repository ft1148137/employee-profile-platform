from datetime import datetime
from uuid import UUID

from sqlmodel import Session, SQLModel, col, select

from app.authz.principal import Principal
from app.domains.audit.models import AuditEvent
from app.domains.change_request.employee import EmployeeRecordNotFound
from app.domains.change_request.models import ProfileChangeRequest, ReviewDecision
from app.domains.change_request.service import list_requests
from app.domains.profile.models import ProfileVersion
from app.models import User


class TimelineEvent(SQLModel):
    at: datetime
    action: str
    via: str | None = None
    client_name: str | None = None
    reviewer: str | None = None
    version: int | None = None
    request_id: UUID | None = None


def request_timeline(
    session: Session, principal: Principal, employee_id: UUID, request_id: UUID
) -> list[TimelineEvent]:
    requests = list_requests(session, principal, employee_id, "all", request_id)
    if not requests.data:
        raise EmployeeRecordNotFound()
    request = requests.data[0]
    events = [TimelineEvent(at=request.created_at, action="created")]
    if request.submitted_at:
        events.append(
            TimelineEvent(
                at=request.submitted_at,
                action="submitted",
                via=request.submitted_via,
                client_name=request.client_name,
            )
        )
    for audit in session.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == principal.org_id,
            AuditEvent.target_type == "profile_change_request",
            AuditEvent.target_id == str(request_id),
            AuditEvent.action == "change_request.withdrawn",
        )
    ).all():
        events.append(TimelineEvent(at=audit.created_at, action="withdrawn"))
    for decision, user in session.exec(
        select(ReviewDecision, User)
        .outerjoin(User, col(User.id) == ReviewDecision.reviewer_user_id)
        .where(
            ReviewDecision.org_id == principal.org_id,
            ReviewDecision.request_id == request_id,
        )
    ).all():
        events.append(
            TimelineEvent(
                at=decision.created_at,
                action="decided",
                via=decision.via,
                client_name=decision.client_name,
                reviewer=(user.full_name or user.username) if user else None,
            )
        )
    for version in session.exec(
        select(ProfileVersion).where(
            ProfileVersion.org_id == principal.org_id,
            ProfileVersion.subject_employee_id == employee_id,
            col(ProfileVersion.published_by)["request_id"].astext == str(request_id),
        )
    ).all():
        events.append(
            TimelineEvent(
                at=version.published_at, action="published", version=version.revision
            )
        )
    if principal.employee_id == employee_id:
        for draft in session.exec(
            select(ProfileChangeRequest).where(
                ProfileChangeRequest.org_id == principal.org_id,
                ProfileChangeRequest.subject_employee_id == employee_id,
                ProfileChangeRequest.parent_request_id == request_id,
            )
        ).all():
            events.append(
                TimelineEvent(
                    at=draft.created_at, action="returned", request_id=draft.id
                )
            )
    return sorted(events, key=lambda event: event.at)
