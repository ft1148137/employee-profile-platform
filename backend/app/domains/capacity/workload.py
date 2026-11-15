from uuid import UUID

from sqlmodel import Session, col, func, select

from app.authz.policies import can_view_employee, employee_scope_clause
from app.authz.principal import Principal
from app.domains.base import utc_now
from app.domains.capacity.models import (
    AssignmentStatus,
    CapacityAssignment,
    CapacityRequestStatus,
    CapacityWorkload,
    CapacityWorkloadRequest,
)
from app.domains.capacity.schemas import (
    MyWorkload,
    Via,
    WorkloadAdjustInput,
    WorkloadDecisionInput,
    WorkloadList,
    WorkloadPublic,
    WorkloadRequestInput,
    WorkloadRequestList,
    WorkloadRequestPublic,
)
from app.domains.capacity.settings import (
    CapacityConflict,
    CapacityForbidden,
    CapacityNotFound,
    audit,
    require_enabled,
)
from app.domains.inbox.models import InboxItem, InboxKind
from app.domains.inbox.service import add_item
from app.domains.org.models import (
    Department,
    Employee,
    EmploymentStatus,
    ExternalIdentity,
)
from app.domains.review.routing import active_delegations, can_review, review_assignment
from app.domains.source.models import SourceConnection
from app.domains.work.models import Task

AVAILABLE_BELOW = 80
OVERLOADED_ABOVE = 100
NOTICE = "你有需求或负荷事项需要处理"


def employee(
    principal: Principal,
    session: Session,
    employee_id: UUID | None,
    *,
    lock: bool = False,
) -> Employee:
    query = select(Employee).where(
        Employee.org_id == principal.org_id, Employee.id == employee_id
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = session.exec(query).one_or_none()
    if row is None or row.employment_status != EmploymentStatus.ACTIVE:
        raise CapacityNotFound()
    return row


def views(
    principal: Principal, session: Session, employees: list[Employee]
) -> list[WorkloadPublic]:
    ids = [e.id for e in employees]
    org_id = principal.org_id
    workloads = {
        w.employee_id: w.base_percent
        for w in session.exec(
            select(CapacityWorkload).where(
                CapacityWorkload.org_id == org_id,
                col(CapacityWorkload.employee_id).in_(ids),
            )
        ).all()
    }
    assignments = dict(
        session.exec(
            select(
                CapacityAssignment.employee_id,
                func.sum(CapacityAssignment.allocation_percent),
            )
            .where(
                CapacityAssignment.org_id == org_id,
                col(CapacityAssignment.employee_id).in_(ids),
                CapacityAssignment.status == AssignmentStatus.ACTIVE,
            )
            .group_by(col(CapacityAssignment.employee_id))
        ).all()
    )
    # shortcut: Unsynced or stale unresolved tasks still count; revisit when Jira sync freshness is exposed.
    jira_connected = session.exec(
        select(
            select(SourceConnection.id)
            .where(SourceConnection.org_id == org_id, SourceConnection.kind == "jira")
            .exists()
        )
    ).one()
    jira = dict(
        session.exec(
            select(ExternalIdentity.employee_id, func.count(func.distinct(Task.id)))
            .outerjoin(
                Task,
                (col(Task.org_id) == ExternalIdentity.org_id)
                & (col(Task.source) == ExternalIdentity.source)
                & col(Task.attributes)["resolved"].astext.is_(None)
                & col(Task.attributes).contains(
                    func.jsonb_build_object(
                        "people",
                        func.jsonb_build_array(
                            func.jsonb_build_object(
                                "id", ExternalIdentity.external_id, "role", "assignee"
                            )
                        ),
                    )
                ),
            )
            .where(
                ExternalIdentity.org_id == org_id,
                jira_connected,
                ExternalIdentity.source == "jira",
                col(ExternalIdentity.employee_id).in_(ids),
            )
            .group_by(col(ExternalIdentity.employee_id))
        ).all()
    )
    departments = {
        d.id: d.name
        for d in session.exec(
            select(Department).where(Department.org_id == org_id)
        ).all()
    }
    result = []
    for e in employees:
        base, platform = workloads.get(e.id), assignments.get(e.id, 0)
        total = None if base is None else base + platform
        result.append(
            WorkloadPublic(
                employee_id=e.id,
                employee_name=e.display_name,
                department_id=e.department_id,
                department_name=departments.get(e.department_id)
                if e.department_id
                else None,
                jira_task_count=jira.get(e.id),
                base_percent=base,
                platform_percent=platform,
                confirmed_percent=total,
                band="unconfirmed"
                if total is None
                else "available"
                if total < AVAILABLE_BELOW
                else "overloaded"
                if total > OVERLOADED_ABOVE
                else "full",
            )
        )
    return result


def get_my_workload(principal: Principal, session: Session) -> MyWorkload:
    require_enabled(principal, session)
    subject = employee(principal, session, principal.employee_id)
    requests = session.exec(
        select(CapacityWorkloadRequest)
        .where(
            CapacityWorkloadRequest.org_id == principal.org_id,
            CapacityWorkloadRequest.employee_id == subject.id,
        )
        .order_by(
            col(CapacityWorkloadRequest.created_at).desc(),
            col(CapacityWorkloadRequest.id),
        )
    ).all()
    return MyWorkload(
        **views(principal, session, [subject])[0].model_dump(),
        requests=[WorkloadRequestPublic.model_validate(r) for r in requests],
    )


def list_workloads(
    principal: Principal, session: Session, department_id: UUID | None = None
) -> WorkloadList:
    require_enabled(principal, session)
    if not principal.is_org_admin and not principal.managed_department_ids:
        raise CapacityForbidden()
    query = (
        select(Employee)
        .outerjoin(
            Department,
            (col(Department.org_id) == Employee.org_id)
            & (col(Department.id) == Employee.department_id),
        )
        .where(employee_scope_clause(principal))
    )
    if department_id is not None:
        if (
            not principal.is_org_admin
            and department_id not in principal.managed_department_ids
        ):
            raise CapacityNotFound()
        query = query.where(Employee.department_id == department_id)
    rows = list(
        session.exec(
            query.order_by(
                col(Department.name), col(Employee.display_name), col(Employee.id)
            )
        ).all()
    )
    data = views(principal, session, rows)
    return WorkloadList(data=data, count=len(data))


def notify_employee(principal: Principal, session: Session, employee_id: UUID) -> None:
    add_item(
        session,
        InboxItem(
            org_id=principal.org_id,
            recipient_employee_id=employee_id,
            kind=InboxKind.CAPACITY,
            message=NOTICE,
        ),
    )


def set_base(
    principal: Principal, session: Session, employee_id: UUID, percent: int
) -> int | None:
    if principal.user_id is None:
        raise CapacityForbidden()
    row = session.exec(
        select(CapacityWorkload).where(
            CapacityWorkload.org_id == principal.org_id,
            CapacityWorkload.employee_id == employee_id,
        )
    ).one_or_none()
    before = row.base_percent if row else None
    if row is None:
        row = CapacityWorkload(
            org_id=principal.org_id,
            employee_id=employee_id,
            base_percent=percent,
            updated_by_user_id=principal.user_id,
        )
    row.base_percent, row.updated_at, row.updated_by_user_id = (
        percent,
        utc_now(),
        principal.user_id,
    )
    session.add(row)
    session.flush()
    return before


def adjust_workload(
    principal: Principal,
    session: Session,
    employee_id: UUID,
    body: WorkloadAdjustInput,
    *,
    via: Via = "web",
    client_name: str | None = None,
) -> WorkloadPublic:
    require_enabled(principal, session)
    subject = employee(principal, session, employee_id, lock=True)
    if not can_view_employee(principal, subject):
        raise CapacityNotFound()
    if (
        subject.id == principal.employee_id
        or subject.department_id not in principal.managed_department_ids
    ):
        raise CapacityForbidden()
    before = set_base(principal, session, subject.id, body.base_percent)
    audit(
        principal,
        session,
        "capacity.workload_adjusted",
        "employee",
        subject.id,
        via,
        client_name,
        {"before_percent": before, "after_percent": body.base_percent},
    )
    notify_employee(principal, session, subject.id)
    return views(principal, session, [subject])[0]


def request_workload_change(
    principal: Principal,
    session: Session,
    requested_percent: int,
    reason: str,
    *,
    via: Via = "web",
    client_name: str | None = None,
) -> WorkloadRequestPublic:
    body = WorkloadRequestInput(requested_percent=requested_percent, reason=reason)
    require_enabled(principal, session)
    subject = employee(principal, session, principal.employee_id, lock=True)
    pending = session.exec(
        select(CapacityWorkloadRequest.id).where(
            CapacityWorkloadRequest.org_id == principal.org_id,
            CapacityWorkloadRequest.employee_id == subject.id,
            CapacityWorkloadRequest.status == CapacityRequestStatus.PENDING,
        )
    ).first()
    if pending:
        raise CapacityConflict()
    row = CapacityWorkloadRequest(
        org_id=principal.org_id, employee_id=subject.id, **body.model_dump()
    )
    session.add(row)
    managers, _ = review_assignment(session, principal.org_id, subject.id)
    delegates = active_delegations(session, principal.org_id, managers)
    for user_id in sorted(set(managers) | {d.delegate_user_id for d in delegates}):
        if user_id != principal.user_id:
            add_item(
                session,
                InboxItem(
                    org_id=principal.org_id,
                    recipient_user_id=user_id,
                    kind=InboxKind.CAPACITY,
                    message=NOTICE,
                ),
            )
    base = session.exec(
        select(CapacityWorkload.base_percent).where(
            CapacityWorkload.org_id == principal.org_id,
            CapacityWorkload.employee_id == subject.id,
        )
    ).one_or_none()
    audit(
        principal,
        session,
        "capacity.workload_requested",
        "capacity_workload_request",
        row.id,
        via,
        client_name,
        {"before_percent": base, "after_percent": body.requested_percent},
    )
    return WorkloadRequestPublic.model_validate(row)


def list_workload_requests(
    principal: Principal, session: Session
) -> WorkloadRequestList:
    require_enabled(principal, session)
    rows = session.exec(
        select(CapacityWorkloadRequest)
        .join(
            Employee,
            (col(Employee.org_id) == CapacityWorkloadRequest.org_id)
            & (col(Employee.id) == CapacityWorkloadRequest.employee_id),
        )
        .where(
            CapacityWorkloadRequest.org_id == principal.org_id,
            Employee.employment_status == EmploymentStatus.ACTIVE,
        )
        .order_by(
            col(CapacityWorkloadRequest.created_at).desc(),
            col(CapacityWorkloadRequest.id),
        )
    ).all()
    data = [
        WorkloadRequestPublic.model_validate(r)
        for r in rows
        if (
            r.status == CapacityRequestStatus.PENDING
            or r.decided_by_user_id == principal.user_id
            or r.decided_for_user_id == principal.user_id
        )
        and can_review(session, principal, r.employee_id) is not None
    ]
    return WorkloadRequestList(data=data, count=len(data))


def decide_workload_request(
    principal: Principal,
    session: Session,
    request_id: UUID,
    body: WorkloadDecisionInput,
    *,
    via: Via = "web",
    client_name: str | None = None,
) -> WorkloadRequestPublic:
    require_enabled(principal, session)
    row = session.exec(
        select(CapacityWorkloadRequest).where(
            CapacityWorkloadRequest.org_id == principal.org_id,
            CapacityWorkloadRequest.id == request_id,
        )
    ).one_or_none()
    if row is None:
        raise CapacityNotFound()
    employee(principal, session, row.employee_id, lock=True)
    manager_id = can_review(session, principal, row.employee_id)
    if manager_id is None:
        raise CapacityNotFound()
    row = session.exec(
        select(CapacityWorkloadRequest)
        .where(
            CapacityWorkloadRequest.org_id == principal.org_id,
            CapacityWorkloadRequest.id == request_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    if row.status != CapacityRequestStatus.PENDING:
        raise CapacityConflict()
    before = session.exec(
        select(CapacityWorkload.base_percent).where(
            CapacityWorkload.org_id == principal.org_id,
            CapacityWorkload.employee_id == row.employee_id,
        )
    ).one_or_none()
    if body.approve:
        set_base(principal, session, row.employee_id, row.requested_percent)
    row.status = (
        CapacityRequestStatus.APPROVED
        if body.approve
        else CapacityRequestStatus.RETURNED
    )
    row.decided_by_user_id, row.decided_for_user_id = (
        principal.user_id,
        manager_id if manager_id != principal.user_id else None,
    )
    row.decided_at, row.decision_note = utc_now(), body.note
    session.add(row)
    audit(
        principal,
        session,
        "capacity.workload_request_approved"
        if body.approve
        else "capacity.workload_request_returned",
        "capacity_workload_request",
        row.id,
        via,
        client_name,
        {
            "before_percent": before,
            "after_percent": row.requested_percent if body.approve else before,
        },
    )
    notify_employee(principal, session, row.employee_id)
    return WorkloadRequestPublic.model_validate(row)
