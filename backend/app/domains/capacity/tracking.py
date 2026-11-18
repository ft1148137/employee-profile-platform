from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import tuple_
from sqlmodel import Session, col, or_, select

from app.authz.policies import employee_scope_clause
from app.authz.principal import Principal
from app.domains.base import utc_now
from app.domains.capacity.assignment import assignment_lender
from app.domains.capacity.demand import demand_row, responder_user_ids
from app.domains.capacity.models import (
    AssignmentStatus,
    CapacityAssignment,
    CapacityAssignmentTask,
    CapacityDemand,
    CapacitySetting,
    CapacityUnavailability,
    TrackingState,
)
from app.domains.capacity.schemas import CapacityInput
from app.domains.capacity.settings import (
    CapacityConflict,
    CapacityForbidden,
    CapacityNotFound,
    require_enabled,
)
from app.domains.inbox.models import InboxItem, InboxKind
from app.domains.inbox.service import add_item
from app.domains.org.models import Employee, EmploymentStatus, ExternalIdentity
from app.domains.org.timezone import read_timezone
from app.domains.work.models import Task

TRACKING_NOTICE = "分配任务跟踪"
UNAVAILABILITY_NOTICE = "人员不可用影响"
STATE_LABELS = {
    TrackingState.NO_TASKS: "尚未关联任务",
    TrackingState.ON_TRACK: "任务进展正常",
    TrackingState.TASKS_DONE: "关联任务已全部完成，可以结束分配",
    TrackingState.OVERDUE: "分配有延期风险",
    TrackingState.HANDED_OVER: "关联任务已交接，请核对分配",
}
CSV_DONE = {"done", "completed", "closed", "完成", "已完成", "已关闭"}


class TrackingTask(CapacityInput):
    id: UUID
    source: str
    external_key: str
    title: str


class AssignmentTracking(CapacityInput):
    tracking_state: TrackingState
    tracking_changed_at: datetime | None
    tasks: list[TrackingTask]


class TaskLinkInput(CapacityInput):
    task_id: UUID


class DemandTrackingRisk(CapacityInput):
    message: str | None


def assignment_for_viewer(
    principal: Principal, session: Session, assignment_id: UUID, *, lock: bool = False
) -> CapacityAssignment:
    require_enabled(principal, session)
    query = (
        select(CapacityAssignment, CapacityDemand.target_department_id)
        .join(
            Employee,
            (col(Employee.org_id) == CapacityAssignment.org_id)
            & (col(Employee.id) == CapacityAssignment.employee_id),
        )
        .join(
            CapacityDemand,
            (col(CapacityDemand.org_id) == CapacityAssignment.org_id)
            & (col(CapacityDemand.id) == CapacityAssignment.demand_id),
        )
        .where(
            employee_scope_clause(principal),
            CapacityAssignment.id == assignment_id,
            Employee.employment_status == EmploymentStatus.ACTIVE,
            or_(
                Employee.id == principal.employee_id,
                col(Employee.department_id).in_(principal.managed_department_ids),
            ),
        )
    )
    if lock:
        query = query.with_for_update(of=CapacityAssignment).execution_options(
            populate_existing=True
        )
    result = session.exec(query).one_or_none()
    if result is None:
        raise CapacityNotFound()
    row, target = result
    if row.employee_id != principal.employee_id and not (
        target in principal.managed_department_ids
        or assignment_lender(session, row) is not None
    ):
        raise CapacityNotFound()
    return row


def assignment_tasks(session: Session, row: CapacityAssignment) -> list[Task]:
    return list(
        session.exec(
            select(Task)
            .join(
                CapacityAssignmentTask,
                (col(CapacityAssignmentTask.org_id) == Task.org_id)
                & (col(CapacityAssignmentTask.task_id) == Task.id),
            )
            .where(
                Task.org_id == row.org_id,
                CapacityAssignmentTask.assignment_id == row.id,
            )
            .order_by(col(Task.external_key), col(Task.id))
        )
    )


def public_tracking(session: Session, row: CapacityAssignment) -> AssignmentTracking:
    return AssignmentTracking(
        tracking_state=row.tracking_state or TrackingState.NO_TASKS,
        tracking_changed_at=row.tracking_changed_at,
        tasks=[
            TrackingTask.model_validate(task) for task in assignment_tasks(session, row)
        ],
    )


def search_tasks(session: Session, org_id: UUID, query: str) -> list[TrackingTask]:
    return [
        TrackingTask.model_validate(task)
        for task in session.exec(
            select(Task)
            .where(
                Task.org_id == org_id,
                or_(
                    col(Task.external_key).icontains(query, autoescape=True),
                    col(Task.title).icontains(query, autoescape=True),
                ),
            )
            .order_by(col(Task.external_key), col(Task.source), col(Task.id))
            .limit(30)
        )
    ]


def set_task_link(
    principal: Principal,
    session: Session,
    assignment_id: UUID,
    task_id: UUID,
    *,
    link: bool,
) -> AssignmentTracking:
    row = assignment_for_viewer(principal, session, assignment_id, lock=True)
    if row.status != AssignmentStatus.ACTIVE:
        raise CapacityConflict()
    if principal.user_id is None:
        raise CapacityForbidden()
    existing = session.exec(
        select(CapacityAssignmentTask).where(
            CapacityAssignmentTask.org_id == row.org_id,
            CapacityAssignmentTask.assignment_id == row.id,
            CapacityAssignmentTask.task_id == task_id,
        )
    ).one_or_none()
    if link and existing is None:
        if (
            session.exec(
                select(Task.id).where(Task.org_id == row.org_id, Task.id == task_id)
            ).one_or_none()
            is None
        ):
            raise CapacityNotFound("未找到该任务")
        session.add(
            CapacityAssignmentTask(
                org_id=row.org_id,
                assignment_id=row.id,
                task_id=task_id,
                linked_by_user_id=principal.user_id,
            )
        )
    if not link and existing is not None:
        session.delete(existing)
    session.flush()
    refresh_assignment(session, row, today(session, row.org_id))
    return public_tracking(session, row)


def completed(task: Task) -> bool | None:
    if task.source == "jira":
        category = task.attributes.get("status_category")
        if category == "done":
            return True
        if category in ("new", "indeterminate"):
            return False
        return None
    if task.source.startswith("csv:") and task.status and task.status.strip():
        return task.status.strip().casefold() in CSV_DONE
    return None


def owner_key(task: Task) -> tuple[str, str] | None:
    # The current source snapshot replaces previous people even when historical events remain.
    people = task.attributes.get("people")
    if not isinstance(people, list):
        return None
    for index, person in enumerate(people):
        if isinstance(person, dict) and (
            (task.source == "jira" and person.get("role") == "assignee")
            or (task.source.startswith("csv:") and index == 0)
        ):
            key = person.get("id")
            if isinstance(key, str):
                return task.source, key
    return None


def tracking_state(
    session: Session, row: CapacityAssignment, tasks: list[Task], current: date
) -> TrackingState:
    if not tasks:
        return TrackingState.NO_TASKS
    done = [completed(task) for task in tasks]
    if all(value is True for value in done):
        return TrackingState.TASKS_DONE
    keys = {key for task in tasks if (key := owner_key(task)) is not None}
    if (
        keys
        and session.exec(
            select(ExternalIdentity.id)
            .where(
                ExternalIdentity.org_id == row.org_id,
                tuple_(
                    col(ExternalIdentity.source), col(ExternalIdentity.external_id)
                ).in_(keys),
                col(ExternalIdentity.employee_id).is_not(None),
                ExternalIdentity.employee_id != row.employee_id,
            )
            .limit(1)
        ).first()
        is not None
    ):
        return TrackingState.HANDED_OVER
    if current > row.due_date and False in done:
        return TrackingState.OVERDUE
    return TrackingState.ON_TRACK


def today(session: Session, org_id: UUID) -> date:
    return (
        utc_now().astimezone(ZoneInfo(read_timezone(session, org_id).timezone)).date()
    )


def refresh_assignment(
    session: Session, row: CapacityAssignment, current: date
) -> None:
    state = tracking_state(session, row, assignment_tasks(session, row), current)
    if state == row.tracking_state:
        return
    row.tracking_state = state
    row.tracking_changed_at = utc_now()
    demand = session.exec(
        select(CapacityDemand).where(
            CapacityDemand.org_id == row.org_id, CapacityDemand.id == row.demand_id
        )
    ).one()
    notify_managers(session, demand, f"{TRACKING_NOTICE}：{STATE_LABELS[state]}。")


def recompute(session: Session, org_id: UUID) -> None:
    enabled = session.exec(
        select(CapacitySetting.enabled).where(CapacitySetting.org_id == org_id)
    ).first()
    if not enabled:
        return
    current = today(session, org_id)
    rows = session.exec(
        select(CapacityAssignment)
        .where(
            CapacityAssignment.org_id == org_id,
            CapacityAssignment.status == AssignmentStatus.ACTIVE,
        )
        .order_by(col(CapacityAssignment.id))
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    for row in rows:
        refresh_assignment(session, row, current)


def demand_tracking_risk(
    principal: Principal, session: Session, demand_id: UUID
) -> DemandTrackingRisk:
    demand = demand_row(principal, session, demand_id)
    risk = session.exec(
        select(CapacityAssignment.id)
        .where(
            CapacityAssignment.org_id == demand.org_id,
            CapacityAssignment.demand_id == demand.id,
            CapacityAssignment.status == AssignmentStatus.ACTIVE,
            col(CapacityAssignment.tracking_state).in_(
                (TrackingState.OVERDUE, TrackingState.HANDED_OVER)
            ),
        )
        .limit(1)
    ).first()
    return DemandTrackingRisk(message="该角色有延期风险" if risk else None)


def notify_managers(session: Session, demand: CapacityDemand, message: str) -> None:
    label = demand.role_name or demand.capability_name
    for user_id in sorted(
        responder_user_ids(session, demand.org_id, demand.target_department_id)
    ):
        add_item(
            session,
            InboxItem(
                org_id=demand.org_id,
                recipient_user_id=user_id,
                kind=InboxKind.CAPACITY,
                message=f"{message} 需求：{label}\n/capacity?tab=incoming&demand={demand.id}&plans=1",
            ),
        )


def notify_unavailability(session: Session, absence: CapacityUnavailability) -> None:
    from app.domains.capacity.project import unavailability_impact

    for row, demand in session.exec(
        select(CapacityAssignment, CapacityDemand)
        .join(
            CapacityDemand,
            (col(CapacityDemand.org_id) == CapacityAssignment.org_id)
            & (col(CapacityDemand.id) == CapacityAssignment.demand_id),
        )
        .where(
            CapacityAssignment.org_id == absence.org_id,
            CapacityAssignment.employee_id == absence.employee_id,
            CapacityAssignment.status == AssignmentStatus.ACTIVE,
            CapacityAssignment.due_date >= absence.start_date,
            or_(
                col(CapacityAssignment.start_date) <= absence.end_date,
                col(CapacityAssignment.start_date).is_(None),
            ),
        )
    ):
        start = max(
            absence.start_date,
            row.start_date or demand.committed_start_date or demand.window_start,
        )
        end = min(absence.end_date, row.due_date)
        if start > end:
            continue
        days = (end - start).days + 1
        message = f"{UNAVAILABILITY_NOTICE}：受影响 {days} 天（{start} 至 {end}）。"
        if demand.project_id is not None:
            projected_end = date.fromordinal(
                min(date.max.toordinal(), row.due_date.toordinal() + days)
            )
            downstream, delay = unavailability_impact(session, demand, projected_end)
            if downstream:
                message += f"受影响的下游角色：{'、'.join(downstream)}。"
            message += f"项目预计比原窗口推迟 {delay} 天。"
        notify_managers(session, demand, message + "可重新模拟或发起变更提议。")
