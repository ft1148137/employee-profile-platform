from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.deps import SessionDep
from app.authz.deps import OrgPrincipalDep
from app.domains.capacity import tracking
from app.domains.capacity.tracking import (
    AssignmentTracking,
    DemandTrackingRisk,
    TaskLinkInput,
    TrackingTask,
)

router = APIRouter(prefix="/orgs/{org_id}/capacity", tags=["capacity-tracking"])


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
