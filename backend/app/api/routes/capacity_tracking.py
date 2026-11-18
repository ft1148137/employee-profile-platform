from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.api.deps import SessionDep
from app.authz.deps import OrgPrincipalDep
from app.domains.capacity import tracking
from app.domains.capacity.tracking import (
    ActiveBorrowAssignment,
    AssignmentTracking,
    DemandTrackingRisk,
    TaskLinkInput,
    TrackingTask,
)
from app.domains.change_request import assignment_feedback
from app.domains.change_request.assignment_feedback import FeedbackInput

router = APIRouter(prefix="/orgs/{org_id}/capacity", tags=["capacity-tracking"])


class FeedbackAssignment(BaseModel):
    id: UUID
    employee_name: str
    demand_title: str
    due_date: date
    default_keys: list[str]
    has_feedback: bool


@router.get("/assignments/active-borrows")
def list_active_borrow_assignments(
    principal: OrgPrincipalDep, session: SessionDep
) -> list[ActiveBorrowAssignment]:
    return tracking.list_active_borrow_assignments(principal, session)


@router.get("/assignments/feedback")
def list_feedback_assignments(
    principal: OrgPrincipalDep, session: SessionDep, demand_id: UUID | None = None
) -> list[FeedbackAssignment]:
    rows = tracking.feedback_targets(principal, session, demand_id=demand_id)
    recorded = assignment_feedback.recorded_feedback(
        session, principal.org_id, [row.id for row in rows]
    )
    return [
        FeedbackAssignment(
            id=row.id,
            employee_name=row.employee_name,
            demand_title=row.demand_title,
            due_date=row.due_date,
            default_keys=row.default_keys,
            has_feedback=str(row.id) in recorded,
        )
        for row in rows
    ]


@router.post("/assignments/{assignment_id}/feedback", status_code=201)
def create_assignment_feedback(
    principal: OrgPrincipalDep,
    session: SessionDep,
    assignment_id: UUID,
    body: FeedbackInput,
) -> UUID:
    (target,) = tracking.feedback_targets(
        principal, session, assignment_id=assignment_id
    )
    request_id = assignment_feedback.create_feedback(
        principal,
        session,
        body,
        assignment_id=target.id,
        employee_id=target.employee_id,
        closed_at=target.closed_at,
        levels=target.levels,
        requester_user_id=target.requester_user_id,
        requesting_department_id=target.requesting_department_id,
    )
    session.commit()
    return request_id


@router.get("/assignments/{assignment_id}/tracking")
def get_assignment_tracking(
    principal: OrgPrincipalDep, session: SessionDep, assignment_id: UUID
) -> AssignmentTracking:
    row = tracking.assignment_for_viewer(principal, session, assignment_id)
    return tracking.public_tracking(session, row)


@router.get("/assignments/{assignment_id}/tasks/search")
def search_assignment_tasks(
    principal: OrgPrincipalDep,
    session: SessionDep,
    assignment_id: UUID,
    q: Annotated[str, Query(min_length=1, max_length=100)],
) -> list[TrackingTask]:
    row = tracking.assignment_for_viewer(principal, session, assignment_id)
    return tracking.search_tasks(session, row.org_id, q.strip()) if q.strip() else []


@router.post("/assignments/{assignment_id}/tasks")
def link_assignment_task(
    principal: OrgPrincipalDep,
    session: SessionDep,
    assignment_id: UUID,
    body: TaskLinkInput,
) -> AssignmentTracking:
    result = tracking.set_task_link(
        principal, session, assignment_id, body.task_id, link=True
    )
    session.commit()
    return result


@router.delete("/assignments/{assignment_id}/tasks/{task_id}")
def unlink_assignment_task(
    principal: OrgPrincipalDep, session: SessionDep, assignment_id: UUID, task_id: UUID
) -> AssignmentTracking:
    result = tracking.set_task_link(
        principal, session, assignment_id, task_id, link=False
    )
    session.commit()
    return result


@router.get("/demands/{demand_id}/tracking")
def get_demand_tracking_risk(
    principal: OrgPrincipalDep, session: SessionDep, demand_id: UUID
) -> DemandTrackingRisk:
    return tracking.demand_tracking_risk(principal, session, demand_id)
