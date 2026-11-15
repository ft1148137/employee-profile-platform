from datetime import date
from uuid import UUID

from fastapi import HTTPException
from sqlmodel import Session, col, select

from app.authz.policies import can_view_employee, employee_scope_clause
from app.authz.principal import Principal
from app.domains.base import utc_now
from app.domains.capacity.assignment_schemas import (
    AssignmentInput,
    AssignmentPublic,
    AssignmentUpdateInput,
    DemandAssignment,
    DemandAssignmentList,
    MyAssignment,
    MyAssignmentList,
)
from app.domains.capacity.demand import (
    demand_row,
    represents_department,
    responder_user_ids,
    validate_department_text,
)
from app.domains.capacity.models import (
    AssignmentStatus,
    CapacityAssignment,
    CapacityDemand,
    CapacityDemandEvent,
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
from app.domains.org.models import Department, Employee
from app.domains.org.service import managed_scope


class CommitmentConfirmationRequired(HTTPException):
    def __init__(self, detail: str) -> None:
        super().__init__(409, detail)


def assignment_rows(
    principal: Principal, session: Session, assignment_id: UUID
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
) -> None:
    """Apply a date change after assignment_rows has established the lock order."""
    if principal.user_id is None:
        raise CapacityNotFound()
    session.refresh(demand, with_for_update=True)
    old = demand.committed_end_date
    if old is None:
        raise CapacityConflict()
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
    subject = employee(principal, session, body.employee_id, lock=True)
    target_ids = managed_scope(
        session, principal.org_id, frozenset({demand.target_department_id})
    )
    if (
        not can_view_employee(principal, subject)
        or subject.department_id not in target_ids
    ):
        raise CapacityNotFound()
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
        )
    ).first()
    if existing:
        raise CapacityConflict()
    row = CapacityAssignment(
        org_id=principal.org_id,
        demand_id=demand.id,
        assigned_by_user_id=principal.user_id,
        **body.model_dump(),
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
        {},
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
) -> AssignmentPublic:
    row, demand = assignment_rows(principal, session, assignment_id)
    subject = employee(principal, session, row.employee_id)
    if (
        not can_view_employee(principal, subject)
        or subject.department_id not in principal.managed_department_ids
    ):
        raise CapacityNotFound()
    if row.status != AssignmentStatus.ACTIVE:
        raise CapacityConflict()
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
    elif body.status is not None:
        row.status, row.closed_at = AssignmentStatus(body.status), utc_now()
        session.add(row)
    audit(
        principal,
        session,
        "capacity.assignment_updated",
        "capacity_assignment",
        row.id,
        via,
        client_name,
        {"due_date": row.due_date.isoformat(), "status": row.status},
    )
    session.flush()
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
    rows = session.exec(
        select(CapacityAssignment, Employee.display_name)
        .join(
            Employee,
            (col(Employee.org_id) == CapacityAssignment.org_id)
            & (col(Employee.id) == CapacityAssignment.employee_id),
        )
        .where(
            CapacityAssignment.org_id == principal.org_id,
            CapacityAssignment.demand_id == demand.id,
            employee_scope_clause(principal),
        )
        .order_by(Employee.display_name, col(CapacityAssignment.id))
    ).all()
    data = [
        DemandAssignment(
            **AssignmentPublic.model_validate(row).model_dump(), employee_name=name
        )
        for row, name in rows
    ]
    return DemandAssignmentList(data=data, count=len(data))
