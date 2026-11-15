from datetime import date
from uuid import UUID

from sqlmodel import Session, col, select

from app.authz.principal import Principal
from app.domains.base import utc_now
from app.domains.capacity.assignment import assignment_rows, change_due_date
from app.domains.capacity.extension_schemas import (
    ExtensionDecisionInput,
    ExtensionInput,
    ExtensionList,
    ExtensionPublic,
)
from app.domains.capacity.models import (
    AssignmentStatus,
    CapacityAssignment,
    CapacityDemand,
    CapacityExtensionRequest,
    CapacityRequestStatus,
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
from app.domains.org.models import Employee, EmploymentStatus
from app.domains.review.routing import active_delegations, can_review, review_assignment


def extension_public(
    request: CapacityExtensionRequest,
    row: CapacityAssignment,
    demand: CapacityDemand,
    employee_name: str,
) -> ExtensionPublic:
    return ExtensionPublic(
        **request.model_dump(),
        employee_name=employee_name,
        demand_id=demand.id,
        capability_name=demand.capability_name,
        current_due_date=row.due_date,
        committed_end_date=demand.committed_end_date,
    )


def request_extension(
    principal: Principal,
    session: Session,
    assignment_id: UUID,
    requested_due_date: date,
    reason: str,
    *,
    via: Via = "web",
    client_name: str | None = None,
) -> ExtensionPublic:
    body = ExtensionInput(requested_due_date=requested_due_date, reason=reason)
    row, demand = assignment_rows(principal, session, assignment_id)
    if row.employee_id != principal.employee_id or principal.user_id is None:
        raise CapacityNotFound()
    if row.status != AssignmentStatus.ACTIVE or body.requested_due_date <= row.due_date:
        raise CapacityConflict()
    pending = session.exec(
        select(CapacityExtensionRequest.id).where(
            CapacityExtensionRequest.org_id == principal.org_id,
            CapacityExtensionRequest.assignment_id == row.id,
            CapacityExtensionRequest.status == CapacityRequestStatus.PENDING,
        )
    ).first()
    if pending:
        raise CapacityConflict()
    request = CapacityExtensionRequest(
        org_id=principal.org_id, assignment_id=row.id, **body.model_dump()
    )
    session.add(request)
    managers, _ = review_assignment(session, principal.org_id, row.employee_id)
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
    audit(
        principal,
        session,
        "capacity.extension_requested",
        "capacity_extension_request",
        request.id,
        via,
        client_name,
        {"requested_due_date": body.requested_due_date.isoformat()},
    )
    return extension_public(
        request, row, demand, employee(principal, session, row.employee_id).display_name
    )


def list_extension_requests(principal: Principal, session: Session) -> ExtensionList:
    require_enabled(principal, session)
    rows = session.exec(
        select(
            CapacityExtensionRequest,
            CapacityAssignment,
            CapacityDemand,
            Employee.display_name,
        )
        .join(
            CapacityAssignment,
            (col(CapacityAssignment.org_id) == CapacityExtensionRequest.org_id)
            & (col(CapacityAssignment.id) == CapacityExtensionRequest.assignment_id),
        )
        .join(
            CapacityDemand,
            (col(CapacityDemand.org_id) == CapacityAssignment.org_id)
            & (col(CapacityDemand.id) == CapacityAssignment.demand_id),
        )
        .join(
            Employee,
            (col(Employee.org_id) == CapacityAssignment.org_id)
            & (col(Employee.id) == CapacityAssignment.employee_id),
        )
        .where(
            CapacityExtensionRequest.org_id == principal.org_id,
            Employee.employment_status == EmploymentStatus.ACTIVE,
        )
        .order_by(
            col(CapacityExtensionRequest.created_at).desc(),
            col(CapacityExtensionRequest.id),
        )
    ).all()
    data = [
        extension_public(r, row, demand, name)
        for r, row, demand, name in rows
        if row.employee_id == principal.employee_id
        or (
            (
                r.status == CapacityRequestStatus.PENDING
                or r.decided_by_user_id == principal.user_id
                or r.decided_for_user_id == principal.user_id
            )
            and can_review(session, principal, row.employee_id) is not None
        )
    ]
    return ExtensionList(data=data, count=len(data))


def decide_extension_request(
    principal: Principal,
    session: Session,
    request_id: UUID,
    body: ExtensionDecisionInput,
    *,
    via: Via = "web",
    client_name: str | None = None,
) -> ExtensionPublic:
    require_enabled(principal, session)
    request = session.exec(
        select(CapacityExtensionRequest).where(
            CapacityExtensionRequest.org_id == principal.org_id,
            CapacityExtensionRequest.id == request_id,
        )
    ).one_or_none()
    if request is None:
        raise CapacityNotFound()
    row, demand = assignment_rows(principal, session, request.assignment_id)
    session.refresh(request, with_for_update=True)
    manager_id = can_review(session, principal, row.employee_id)
    if manager_id is None:
        raise CapacityNotFound()
    if request.status != CapacityRequestStatus.PENDING:
        raise CapacityConflict()
    before = row.due_date
    if body.approve:
        if (
            row.status != AssignmentStatus.ACTIVE
            or request.requested_due_date <= before
        ):
            raise CapacityConflict()
        change_due_date(
            principal,
            session,
            row,
            demand,
            request.requested_due_date,
            body.new_committed_end_date,
            body.commitment_note,
            via,
            client_name,
        )
        notify_employee(principal, session, row.employee_id)
    else:
        add_item(
            session,
            InboxItem(
                org_id=principal.org_id,
                recipient_employee_id=row.employee_id,
                kind=InboxKind.CAPACITY,
                message=f"延期申请已退回：{body.note}",
            ),
        )
    request.status = (
        CapacityRequestStatus.APPROVED
        if body.approve
        else CapacityRequestStatus.RETURNED
    )
    request.decided_by_user_id = principal.user_id
    request.decided_for_user_id = (
        manager_id if manager_id != principal.user_id else None
    )
    request.decided_at, request.decision_note = utc_now(), body.note
    session.add(request)
    audit(
        principal,
        session,
        "capacity.extension_approved"
        if body.approve
        else "capacity.extension_returned",
        "capacity_extension_request",
        request.id,
        via,
        client_name,
        {"before_date": before.isoformat(), "after_date": row.due_date.isoformat()},
    )
    return extension_public(
        request, row, demand, employee(principal, session, row.employee_id).display_name
    )
