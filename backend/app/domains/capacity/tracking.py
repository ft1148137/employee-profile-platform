from datetime import date, datetime
from typing import NamedTuple
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import and_, tuple_
from sqlalchemy.sql.elements import ColumnElement
from sqlmodel import Session, col, or_, select

from app.authz.policies import employee_scope_clause
from app.authz.principal import Principal
from app.domains.audit.models import ActorKind
from app.domains.base import utc_now
from app.domains.capacity.assignment import assignment_lender
from app.domains.capacity.demand import demand_row, responder_user_ids
from app.domains.capacity.models import (
    AssignmentStatus,
    CapacityAssignment,
    CapacityAssignmentTask,
    CapacityDemand,
    CapacityProposalLine,
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
from app.domains.org.models import (
    Employee,
    EmploymentStatus,
    ExternalIdentity,
    Organization,
)
from app.domains.org.timezone import read_timezone
from app.domains.work.models import Task

TRACKING_NOTICE = "分配任务跟踪"
UNAVAILABILITY_NOTICE = "人员不可用影响"
BORROW_TRACKING_URL = "/capacity?tab=borrows"
STATE_LABELS = {
    TrackingState.NO_TASKS: "尚未关联任务",
    TrackingState.ON_TRACK: "任务进展正常",
    TrackingState.TASKS_DONE: "关联任务已全部完成，可以结束分配",
    TrackingState.OVERDUE: "分配有延期风险",
    TrackingState.HANDED_OVER: "关联任务已交接，请核对分配",
    TrackingState.STATUS_UNKNOWN: "有关联任务状态待确认",
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


class ActiveBorrowAssignment(CapacityInput):
    id: UUID
    employee_name: str
    demand_title: str
    start_date: date | None
    due_date: date


class FeedbackTarget(NamedTuple):
    id: UUID
    employee_id: UUID
    employee_name: str
    demand_title: str
    due_date: date
    closed_at: datetime | None
    levels: dict[str, str | None]
    default_keys: list[str]
    requester_user_id: UUID
    requesting_department_id: UUID | None


class FeedbackNotCompleted(HTTPException):
    def __init__(self) -> None:
        super().__init__(409, "分配完成后才能写反馈")


def feedback_targets(
    principal: Principal,
    session: Session,
    *,
    demand_id: UUID | None = None,
    assignment_id: UUID | None = None,
) -> list[FeedbackTarget]:
    if assignment_id is not None:
        session.exec(
            select(Organization)
            .where(Organization.id == principal.org_id)
            .with_for_update(read=True)
        ).one()
    require_enabled(principal, session)
    query = (
        select(CapacityAssignment, CapacityDemand, Employee.display_name)
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
            principal.actor_type == ActorKind.USER,
            Employee.employment_status == EmploymentStatus.ACTIVE,
            col(Employee.id).is_distinct_from(principal.employee_id),
            or_(
                col(Employee.department_id).in_(principal.managed_department_ids),
                (col(CapacityDemand.requester_user_id) != principal.user_id)
                & ~(
                    col(CapacityDemand.requesting_department_id).in_(
                        principal.managed_department_ids
                    )
                    & (
                        col(CapacityDemand.requesting_department_id)
                        != CapacityDemand.target_department_id
                    )
                ),
            ),
        )
    )
    if assignment_id is not None:
        rows = session.exec(
            query.where(CapacityAssignment.id == assignment_id).with_for_update(
                of=(CapacityAssignment, Employee)
            )
        ).all()
        if not rows:
            raise CapacityNotFound("未找到这条分配，或你没有权限写反馈")
        if rows[0][0].status != AssignmentStatus.DONE:
            raise FeedbackNotCompleted()
    else:
        query = query.where(CapacityAssignment.status == AssignmentStatus.DONE)
        if demand_id is None:
            query = query.join(
                CapacityProposalLine,
                (col(CapacityProposalLine.org_id) == CapacityAssignment.org_id)
                & (col(CapacityProposalLine.id) == CapacityAssignment.proposal_line_id),
            ).where(col(CapacityProposalLine.lender_department_id).is_not(None))
        else:
            query = query.where(
                CapacityDemand.id == demand_id,
                or_(
                    principal.is_org_admin,
                    col(CapacityDemand.target_department_id).in_(
                        principal.managed_department_ids
                    ),
                ),
            )
        rows = session.exec(
            query.order_by(
                col(CapacityAssignment.closed_at).desc(), col(CapacityAssignment.id)
            )
        ).all()
    results = []
    for row, demand, name in rows:
        levels = {demand.capability_key: demand.minimum_level}
        defaults = [demand.capability_key]
        for extra in demand.extra_capabilities:
            if isinstance(key := extra.get("key"), str):
                level = extra.get("minimum_level")
                levels[key] = level if isinstance(level, str) else None
                if extra.get("required"):
                    defaults.append(key)
        results.append(
            FeedbackTarget(
                row.id,
                row.employee_id,
                name,
                demand.role_name or demand.description,
                row.due_date,
                row.closed_at,
                levels,
                defaults,
                demand.requester_user_id,
                demand.requesting_department_id
                if demand.requesting_department_id != demand.target_department_id
                else None,
            )
        )
    return results


def _tracking_employee_scope(principal: Principal) -> ColumnElement[bool]:
    return and_(
        employee_scope_clause(principal),
        col(Employee.employment_status) == EmploymentStatus.ACTIVE,
    )


def list_active_borrow_assignments(
    principal: Principal, session: Session, *, assignment_id: UUID | None = None
) -> list[ActiveBorrowAssignment]:
    require_enabled(principal, session)
    query = (
        select(CapacityAssignment, CapacityDemand, Employee.display_name)
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
        .join(
            CapacityProposalLine,
            (col(CapacityProposalLine.org_id) == CapacityAssignment.org_id)
            & (col(CapacityProposalLine.id) == CapacityAssignment.proposal_line_id),
        )
        .where(
            _tracking_employee_scope(principal),
            col(Employee.department_id).in_(principal.managed_department_ids),
            col(CapacityProposalLine.lender_department_id).is_not(None),
            CapacityAssignment.status == AssignmentStatus.ACTIVE,
        )
    )
    if assignment_id is not None:
        query = query.where(CapacityAssignment.id == assignment_id)
    rows = session.exec(
        query.order_by(
            col(CapacityAssignment.due_date),
            col(Employee.display_name),
            col(CapacityAssignment.id),
        )
    )
    return [
        ActiveBorrowAssignment(
            id=row.id,
            employee_name=name,
            demand_title=demand.role_name or demand.description,
            start_date=row.start_date,
            due_date=row.due_date,
        )
        for row, demand, name in rows
    ]


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
            _tracking_employee_scope(principal),
            CapacityAssignment.id == assignment_id,
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
    refresh_assignment(
        session, row, today(session, row.org_id), exclude_user_id=principal.user_id
    )
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
    if None in done:
        return TrackingState.STATUS_UNKNOWN
    return TrackingState.ON_TRACK


def today(session: Session, org_id: UUID) -> date:
    return (
        utc_now().astimezone(ZoneInfo(read_timezone(session, org_id).timezone)).date()
    )


def refresh_assignment(
    session: Session,
    row: CapacityAssignment,
    current: date,
    *,
    exclude_user_id: UUID | None = None,
) -> None:
    previous_state = row.tracking_state
    state = tracking_state(session, row, assignment_tasks(session, row), current)
    if state == previous_state:
        return
    row.tracking_state = state
    row.tracking_changed_at = utc_now()
    if previous_state is None:
        return
    demand = session.exec(
        select(CapacityDemand).where(
            CapacityDemand.org_id == row.org_id, CapacityDemand.id == row.demand_id
        )
    ).one()
    notify_managers(
        session,
        demand,
        row,
        f"{TRACKING_NOTICE}：{STATE_LABELS[state]}。",
        exclude_user_id=exclude_user_id,
    )


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
    rows = session.exec(
        select(CapacityAssignment.status, CapacityAssignment.tracking_state).where(
            CapacityAssignment.org_id == demand.org_id,
            CapacityAssignment.demand_id == demand.id,
            col(CapacityAssignment.status).in_(
                (AssignmentStatus.ACTIVE, AssignmentStatus.DONE)
            ),
        )
    ).all()
    active = {state for status, state in rows if status == AssignmentStatus.ACTIVE}
    if not rows:
        return DemandTrackingRisk(message=None)
    if active & {TrackingState.OVERDUE, TrackingState.HANDED_OVER}:
        return DemandTrackingRisk(message="该角色有延期风险")
    if TrackingState.STATUS_UNKNOWN in active:
        summary = STATE_LABELS[TrackingState.STATUS_UNKNOWN]
    elif active == {TrackingState.TASKS_DONE}:
        summary = "关联任务已全部完成，待承接部门确认结束"
    elif active & {TrackingState.ON_TRACK, TrackingState.TASKS_DONE}:
        summary = "执行正常"
    elif active:
        summary = STATE_LABELS[TrackingState.NO_TASKS]
    else:
        summary = "已完成"
    return DemandTrackingRisk(message=f"该角色执行跟踪：{summary}")


def notify_managers(
    session: Session,
    demand: CapacityDemand,
    row: CapacityAssignment,
    message: str,
    *,
    exclude_user_id: UUID | None = None,
) -> None:
    label = demand.role_name or demand.capability_name
    start = row.start_date or demand.committed_start_date or demand.window_start
    assignment = f"分配 #{row.id.hex[-8:]}（{start} 至 {row.due_date}，{row.allocation_percent}%）"
    title, _, detail = message.partition("：")
    incoming_url = f"/capacity?tab=incoming&demand={demand.id}&plans=1"
    recipients = dict.fromkeys(
        responder_user_ids(session, demand.org_id, demand.target_department_id),
        incoming_url,
    )
    if assignment_lender(session, row) is not None:
        employee_department = session.exec(
            select(Employee.department_id).where(
                Employee.org_id == row.org_id,
                Employee.id == row.employee_id,
                Employee.employment_status == EmploymentStatus.ACTIVE,
            )
        ).one_or_none()
        if employee_department is not None:
            for user_id in responder_user_ids(
                session, row.org_id, employee_department, fallback_to_admin=False
            ):
                recipients.setdefault(user_id, BORROW_TRACKING_URL)
    for user_id, url in sorted(recipients.items()):
        if user_id == exclude_user_id:
            continue
        add_item(
            session,
            InboxItem(
                org_id=demand.org_id,
                recipient_user_id=user_id,
                kind=InboxKind.CAPACITY,
                message=f"{title}：{assignment}。{detail} 需求：{label}\n{url}",
            ),
            borrow_assignment_id=row.id if url == BORROW_TRACKING_URL else None,
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
            impact = unavailability_impact(session, demand, projected_end)
            if impact.shifted_roles:
                message += f"受影响的下游角色：{'、'.join(impact.shifted_roles)}。"
            if impact.unconfirmed_roles:
                message += (
                    f"日期待确认的下游角色：{'、'.join(impact.unconfirmed_roles)}。"
                )
            elif impact.has_downstream and not impact.shifted_roles:
                message += "下游角色日期暂不受影响。"
            if impact.delay_days is None:
                message += "项目预计完工日待确认。"
            else:
                message += f"项目预计比原窗口推迟 {impact.delay_days} 天。"
        notify_managers(session, demand, row, message + "可重新模拟或发起变更提议。")
