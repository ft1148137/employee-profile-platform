from datetime import date
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, case, or_
from sqlmodel import Session, col, select

from app.authz.policies import can_view_employee, employee_scope_clause
from app.authz.principal import Principal
from app.domains.base import utc_now
from app.domains.capacity import calendar
from app.domains.capacity.assignment_schemas import (
    AnonymousBorrowAssignment,
    AssignmentInput,
    AssignmentPublic,
    AssignmentUpdateInput,
    DemandAssignment,
    DemandAssignmentList,
    DepartedAssignmentCancelled,
    DepartedDemandAssignment,
    MyAssignment,
    MyAssignmentList,
)
from app.domains.capacity.demand import (
    demand_row,
    get_demand_basis,
    represents_department,
    responder_user_ids,
    validate_department_text,
    validate_load_words,
)
from app.domains.capacity.models import (
    AssignmentStatus,
    CapacityAssignment,
    CapacityDemand,
    CapacityDemandEvent,
    CapacityProposalLine,
    DemandEventKind,
    DemandStatus,
)
from app.domains.capacity.schemas import Via
from app.domains.capacity.settings import (
    CapacityConflict,
    CapacityNotFound,
    audit,
    require_enabled,
)
from app.domains.capacity.workload import NOTICE, employee, notify_employee
from app.domains.inbox.models import InboxItem, InboxKind
from app.domains.inbox.service import add_item
from app.domains.org.models import Department, Employee, EmploymentStatus
from app.domains.org.service import managed_scope


class CommitmentConfirmationRequired(HTTPException):
    def __init__(self, detail: str) -> None:
        super().__init__(409, detail)


class LegacyAllocationEditInvalid(HTTPException):
    def __init__(self) -> None:
        super().__init__(422, "旧分配不支持直接修改投入比例")


class DepartedAssignmentConflict(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            409,
            {
                "code": "employee_departed",
                "message": "该员工已不在职，请刷新后重新选择",
            },
        )


def check_dates(demand: CapacityDemand, start_date: date, due_date: date) -> None:
    if start_date > due_date or (
        demand.committed_start_date is not None
        and start_date < demand.committed_start_date
    ):
        raise CommitmentConfirmationRequired(
            "开始日期不能早于承诺开始日期，也不能晚于完成日期"
        )


class AssignmentCapacityConflict(HTTPException):
    def __init__(self, weeks: list[calendar.WeekLoad], allocation_percent: int) -> None:
        unconfirmed, over = calendar.shortfalls(weeks, allocation_percent)
        super().__init__(
            409,
            {
                "message": "请先确认这些周的容量"
                if unconfirmed
                else "这些周的分配超过可用容量",
                "weeks": [
                    {"week_start": w.week_start.isoformat(), "status": w.status}
                    for w in weeks
                    if w.week_start in unconfirmed
                ],
                "over_weeks": [
                    {"week_start": w.isoformat(), "over_percent": percent}
                    for w, percent in over
                ],
            },
        )


def check_capacity(
    session: Session,
    org_id: UUID,
    employee_id: UUID,
    start_date: date,
    due_date: date,
    allocation_percent: int,
    exclude_assignment_id: UUID | None = None,
) -> None:
    start = max(calendar.week_start(start_date), calendar.this_week(session, org_id))
    if due_date < start:
        return
    weeks = calendar.loads(
        session,
        org_id,
        [employee_id],
        start,
        (calendar.week_start(due_date) - start).days // 7 + 1,
        exclude_assignment_id=exclude_assignment_id,
    )[employee_id]
    unconfirmed, over = calendar.shortfalls(weeks, allocation_percent)
    if unconfirmed or over:
        raise AssignmentCapacityConflict(weeks, allocation_percent)


def assignment_lender(session: Session, row: CapacityAssignment) -> UUID | None:
    return (
        session.exec(
            select(CapacityProposalLine.lender_department_id).where(
                CapacityProposalLine.org_id == row.org_id,
                CapacityProposalLine.id == row.proposal_line_id,
            )
        ).first()
        if row.proposal_line_id
        else None
    )


def assignment_rows(
    principal: Principal,
    session: Session,
    assignment_id: UUID,
    *,
    allow_departed: bool = False,
    allow_borrow: bool = False,
) -> tuple[CapacityAssignment, CapacityDemand]:
    """Lock demand before assignment; callers enforce their operation's permission."""
    require_enabled(principal, session)
    row = session.exec(
        select(CapacityAssignment).where(
            CapacityAssignment.org_id == principal.org_id,
            CapacityAssignment.id == assignment_id,
        )
    ).one_or_none()
    if row is None:
        raise CapacityNotFound()
    # All assignment writers lock the demand first, serializing the commitment check.
    demand = session.exec(
        select(CapacityDemand)
        .where(
            CapacityDemand.org_id == principal.org_id,
            CapacityDemand.id == row.demand_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    session.refresh(row, with_for_update=True)
    if allow_departed or (allow_borrow and assignment_lender(session, row) is not None):
        subject = session.exec(
            select(Employee)
            .where(Employee.org_id == principal.org_id, Employee.id == row.employee_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one_or_none()
        if subject is None or (
            not allow_departed and subject.employment_status != EmploymentStatus.ACTIVE
        ):
            raise CapacityNotFound()
    else:
        employee(principal, session, row.employee_id, lock=True)
    return row, demand


def change_due_date(
    principal: Principal,
    session: Session,
    row: CapacityAssignment,
    demand: CapacityDemand,
    due_date: date,
    new_committed_end_date: date | None,
    commitment_note: str | None,
    via: Via,
    client_name: str | None,
    *,
    lender_department_id: UUID | None = None,
) -> None:
    """Apply a date change after assignment_rows has established the lock order."""
    if principal.user_id is None:
        raise CapacityNotFound()
    session.refresh(demand, with_for_update=True)
    old = demand.committed_end_date
    if old is None:
        raise CapacityConflict()
    subject_department = (
        employee(principal, session, row.employee_id).department_id
        if lender_department_id is None
        else session.exec(
            select(Employee.department_id).where(
                Employee.org_id == row.org_id,
                Employee.id == row.employee_id,
            )
        ).one()
    )
    if subject_department not in managed_scope(
        session,
        principal.org_id,
        frozenset({lender_department_id or demand.target_department_id}),
    ):
        raise CapacityNotFound()
    start = row.start_date or calendar.week_start(row.created_at.date())
    if row.start_date is not None:
        check_dates(demand, start, due_date)
    elif start > due_date:
        raise CommitmentConfirmationRequired("请先确认分配的开始日期")
    check_capacity(
        session,
        principal.org_id,
        row.employee_id,
        start,
        due_date,
        row.allocation_percent,
        row.id,
    )
    if due_date > old:
        detail = (
            "会推迟对需求方的承诺：请同时填写新的承诺日期（不早于本需求各分配的完成日期）和对外说明"
            if represents_department(principal, demand.target_department_id)
            else "会推迟对需求方的承诺，你不能确认新的承诺日期：请把审批代理给目标部门经理，或退回让员工改到承诺日期之内"
        )
        if (
            commitment_note is None
            or new_committed_end_date is None
            or new_committed_end_date < due_date
            or not represents_department(principal, demand.target_department_id)
        ):
            raise CommitmentConfirmationRequired(detail)
        others = session.exec(
            select(col(CapacityAssignment.due_date)).where(
                CapacityAssignment.org_id == principal.org_id,
                CapacityAssignment.demand_id == demand.id,
                CapacityAssignment.id != row.id,
                CapacityAssignment.status == AssignmentStatus.ACTIVE,
            )
        ).all()
        if any(d > new_committed_end_date for d in others):
            raise CommitmentConfirmationRequired(detail)
        validate_department_text(
            principal, session, demand.target_department_id, commitment_note
        )
        if lender_department_id is not None:
            validate_department_text(
                principal, session, lender_department_id, commitment_note
            )
        validate_load_words(principal, session, demand, commitment_note)
        demand.committed_end_date = new_committed_end_date
        demand.revision += 1
        session.add(demand)
        session.add(
            CapacityDemandEvent(
                org_id=principal.org_id,
                demand_id=demand.id,
                kind=DemandEventKind.COMMITMENT_CHANGED,
                actor_user_id=principal.user_id,
                text=commitment_note,
                end_date=new_committed_end_date,
                basis={
                    "previous_end_date": old.isoformat(),
                    "revision": demand.revision,
                },
                via=via,
            )
        )
        for user_id in sorted(
            {demand.requester_user_id}
            | responder_user_ids(
                session, principal.org_id, demand.requesting_department_id
            )
        ):
            add_item(
                session,
                InboxItem(
                    org_id=principal.org_id,
                    recipient_user_id=user_id,
                    kind=InboxKind.CAPACITY,
                    message=NOTICE,
                ),
            )
        audit(
            principal,
            session,
            "capacity.commitment_changed",
            "capacity_demand",
            demand.id,
            via,
            client_name,
            {
                "before_date": old.isoformat(),
                "after_date": new_committed_end_date.isoformat(),
                "revision": demand.revision,
            },
        )
    elif new_committed_end_date is not None:
        raise CapacityConflict()
    if (
        row.proposal_line_id is not None
        and due_date != row.due_date
        and due_date <= old
    ):
        demand.revision += 1
        session.add(demand)
    row.due_date = due_date
    session.add(row)


def create_assignment(
    principal: Principal,
    session: Session,
    demand_id: UUID,
    body: AssignmentInput,
    *,
    via: Via = "web",
    client_name: str | None = None,
) -> AssignmentPublic:
    demand = demand_row(principal, session, demand_id, lock=True)
    if principal.user_id is None or not represents_department(
        principal, demand.target_department_id
    ):
        raise CapacityNotFound()
    if demand.status != DemandStatus.ACCEPTED:
        raise CapacityConflict()
    if (
        session.exec(
            select(CapacityAssignment.id).where(
                CapacityAssignment.org_id == principal.org_id,
                CapacityAssignment.demand_id == demand.id,
                col(CapacityAssignment.proposal_line_id).is_not(None),
            )
        ).first()
        is not None
    ):
        raise CommitmentConfirmationRequired("已生效的安排请发起变更")
    subject = session.exec(
        select(Employee)
        .where(Employee.org_id == principal.org_id, Employee.id == body.employee_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if subject is None:
        raise CapacityNotFound()
    target_ids = managed_scope(
        session, principal.org_id, frozenset({demand.target_department_id})
    )
    if subject.employment_status == EmploymentStatus.INACTIVE:
        if (
            demand.target_department_id in principal.managed_department_ids
            and subject.department_id in target_ids
        ):
            raise DepartedAssignmentConflict()
        raise CapacityNotFound()
    if (
        not can_view_employee(principal, subject)
        or subject.department_id not in target_ids
    ):
        raise CapacityNotFound()
    check_dates(demand, body.start_date, body.due_date)
    if demand.committed_end_date is None or body.due_date > demand.committed_end_date:
        raise CommitmentConfirmationRequired(
            f"完成日期不能晚于对需求方的承诺日期 {demand.committed_end_date}；需要推迟承诺时，先在已有分配上调整"
            if demand.committed_end_date is not None
            else "会推迟对需求方的承诺：请同时填写新的承诺日期（不早于本需求各分配的完成日期）和对外说明"
        )
    existing = session.exec(
        select(CapacityAssignment.id).where(
            CapacityAssignment.org_id == principal.org_id,
            CapacityAssignment.demand_id == demand.id,
            CapacityAssignment.employee_id == subject.id,
            CapacityAssignment.status == AssignmentStatus.ACTIVE,
            col(CapacityAssignment.proposal_line_id).is_(None),
        )
    ).first()
    if existing:
        raise CapacityConflict()
    if not body.outside_basis and not any(
        person.employee_id == subject.id
        for person in get_demand_basis(principal, session, demand.id).data
    ):
        raise CommitmentConfirmationRequired("该员工不在依据中，请确认按你的判断安排")
    check_capacity(
        session,
        principal.org_id,
        subject.id,
        body.start_date,
        body.due_date,
        body.allocation_percent,
    )
    row = CapacityAssignment(
        org_id=principal.org_id,
        demand_id=demand.id,
        assigned_by_user_id=principal.user_id,
        **body.model_dump(exclude={"outside_basis"}),
    )
    session.add(row)
    audit(
        principal,
        session,
        "capacity.assignment_created",
        "capacity_assignment",
        row.id,
        via,
        client_name,
        {"outside_basis": body.outside_basis},
    )
    notify_employee(principal, session, row.employee_id)
    session.flush()
    return AssignmentPublic.model_validate(row)


def update_assignment(
    principal: Principal,
    session: Session,
    assignment_id: UUID,
    body: AssignmentUpdateInput,
    *,
    via: Via = "web",
    client_name: str | None = None,
) -> AssignmentPublic | DepartedAssignmentCancelled:
    cancelling = body.status == "cancelled"
    row, demand = assignment_rows(
        principal, session, assignment_id, allow_departed=cancelling
    )
    subject = (
        session.exec(
            select(Employee).where(
                Employee.org_id == principal.org_id, Employee.id == row.employee_id
            )
        ).one()
        if cancelling
        else employee(principal, session, row.employee_id)
    )
    if subject.employment_status == EmploymentStatus.INACTIVE:
        if (
            not cancelling
            or principal.is_org_admin
            or not represents_department(principal, demand.target_department_id)
            or subject.department_id not in principal.managed_department_ids
            or subject.department_id
            not in managed_scope(
                session, principal.org_id, frozenset({demand.target_department_id})
            )
        ):
            raise CapacityNotFound()
    elif (
        not can_view_employee(principal, subject)
        or subject.department_id not in principal.managed_department_ids
    ):
        raise CapacityNotFound()
    if row.status != AssignmentStatus.ACTIVE:
        raise CapacityConflict()
    if body.start_date is not None and subject.department_id not in managed_scope(
        session, principal.org_id, frozenset({demand.target_department_id})
    ):
        raise CapacityNotFound()
    if row.proposal_line_id is not None and (
        (cancelling and subject.employment_status == EmploymentStatus.ACTIVE)
        or body.allocation_percent is not None
        or body.start_date is not None
        or (body.due_date is not None and body.due_date < row.due_date)
    ):
        raise CommitmentConfirmationRequired("已生效的安排请发起变更")
    if body.allocation_percent is not None:
        raise LegacyAllocationEditInvalid()
    if body.due_date:
        change_due_date(
            principal,
            session,
            row,
            demand,
            body.due_date,
            body.new_committed_end_date,
            body.commitment_note,
            via,
            client_name,
        )
        notify_employee(principal, session, row.employee_id)
    elif body.start_date is not None:
        check_dates(demand, body.start_date, row.due_date)
        check_capacity(
            session,
            principal.org_id,
            row.employee_id,
            body.start_date,
            row.due_date,
            row.allocation_percent,
            row.id,
        )
        row.start_date = body.start_date
        session.add(row)
        notify_employee(principal, session, row.employee_id)
    elif body.status is not None:
        row.status, row.closed_at = AssignmentStatus(body.status), utc_now()
        session.add(row)
    if row.proposal_line_id is not None and body.status is not None:
        demand.revision += 1
        session.add(demand)
    audit(
        principal,
        session,
        "capacity.assignment_updated",
        "capacity_assignment",
        row.id,
        via,
        client_name,
        {
            "start_date": row.start_date.isoformat() if row.start_date else None,
            "due_date": row.due_date.isoformat(),
            "status": row.status,
        },
    )
    session.flush()
    if cancelling and subject.employment_status == EmploymentStatus.INACTIVE:
        return DepartedAssignmentCancelled(id=row.id, status="cancelled")
    return AssignmentPublic.model_validate(row)


def list_my_assignments(principal: Principal, session: Session) -> MyAssignmentList:
    from app.domains.capacity.extension import extension_reviewers, user_names

    require_enabled(principal, session)
    subject = employee(principal, session, principal.employee_id)
    rows = session.exec(
        select(CapacityAssignment, CapacityDemand, Department.name)
        .join(
            CapacityDemand,
            (col(CapacityDemand.org_id) == CapacityAssignment.org_id)
            & (col(CapacityDemand.id) == CapacityAssignment.demand_id),
        )
        .join(
            Department,
            (col(Department.org_id) == CapacityDemand.org_id)
            & (col(Department.id) == CapacityDemand.requesting_department_id),
        )
        .where(
            CapacityAssignment.org_id == principal.org_id,
            CapacityAssignment.employee_id == subject.id,
        )
        .order_by(col(CapacityAssignment.created_at).desc(), col(CapacityAssignment.id))
    ).all()
    data = [
        MyAssignment(
            **AssignmentPublic.model_validate(r).model_dump(),
            requesting_department_name=department_name,
            capability_name=d.capability_name,
            description=d.description,
            # shortcut: Resolve per assignment; batch if personal assignment lists grow.
            reviewer_names=list(
                user_names(
                    session,
                    principal.org_id,
                    {u.id for u in extension_reviewers(principal, session, r, d)},
                ).values()
            ),
        )
        for r, d, department_name in rows
    ]
    return MyAssignmentList(data=data, count=len(data))


def list_demand_assignments(
    principal: Principal, session: Session, demand_id: UUID
) -> DemandAssignmentList:
    demand = demand_row(principal, session, demand_id)
    if not represents_department(principal, demand.target_department_id):
        raise CapacityNotFound()
    visible_name = (
        col(Employee.display_name)
        if principal.is_org_admin
        else case(
            (
                col(Employee.employment_status) == EmploymentStatus.INACTIVE,
                "已离职员工",
            ),
            else_=col(Employee.display_name),
        )
    )
    borrowed = col(CapacityProposalLine.lender_department_id).is_not(None)
    rows = session.exec(
        select(CapacityAssignment, Employee, CapacityProposalLine.lender_department_id)
        .join(
            Employee,
            (col(Employee.org_id) == CapacityAssignment.org_id)
            & (col(Employee.id) == CapacityAssignment.employee_id),
        )
        .outerjoin(
            CapacityProposalLine,
            (col(CapacityProposalLine.org_id) == CapacityAssignment.org_id)
            & (col(CapacityProposalLine.id) == CapacityAssignment.proposal_line_id),
        )
        .where(
            CapacityAssignment.org_id == principal.org_id,
            CapacityAssignment.demand_id == demand.id,
            or_(
                borrowed,
                employee_scope_clause(principal),
                and_(
                    col(Employee.employment_status) == EmploymentStatus.INACTIVE,
                    col(CapacityAssignment.status) == AssignmentStatus.ACTIVE,
                    col(Employee.department_id).in_(principal.managed_department_ids),
                    col(Employee.department_id).in_(
                        managed_scope(
                            session,
                            principal.org_id,
                            frozenset({demand.target_department_id}),
                        )
                    ),
                ),
            ),
        )
        .order_by(case((borrowed, ""), else_=visible_name), col(CapacityAssignment.id))
    ).all()
    data = [
        AnonymousBorrowAssignment(
            allocation_percent=row.allocation_percent,
            start_date=row.start_date,
            due_date=row.due_date,
            status=row.status,
            tracking_state=row.tracking_state,
        )
        if lender is not None
        and not (
            subject.id == principal.employee_id
            or (
                subject.department_id in principal.managed_department_ids
                and can_view_employee(principal, subject)
            )
        )
        else DepartedDemandAssignment(
            id=row.id,
            allocation_percent=row.allocation_percent,
            start_date=row.start_date,
            due_date=row.due_date,
            employee_name="已离职员工",
            employee_departed=True,
        )
        if subject.employment_status == EmploymentStatus.INACTIVE
        and not can_view_employee(principal, subject)
        else DemandAssignment(
            **AssignmentPublic.model_validate(row).model_dump(),
            employee_name=subject.display_name,
            employee_departed=subject.employment_status == EmploymentStatus.INACTIVE,
        )
        for row, subject, lender in rows
    ]
    return DemandAssignmentList(data=data, count=len(data))
