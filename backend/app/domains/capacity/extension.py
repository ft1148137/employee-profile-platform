from datetime import date
from uuid import UUID

from fastapi import HTTPException
from sqlmodel import Session, col, or_, select

from app.authz.deps import get_org_principal
from app.authz.policies import employee_scope_clause
from app.authz.principal import Principal
from app.domains.base import utc_now
from app.domains.capacity.assignment import (
    AssignmentCapacityConflict,
    assignment_lender,
    assignment_rows,
    change_due_date,
)
from app.domains.capacity.demand import represents_department, responder_user_ids
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
from app.domains.capacity.workload import (
    EXTENSION_REVIEW_NOTICE,
    employee,
    notify_employee,
)
from app.domains.inbox.models import InboxItem, InboxKind
from app.domains.inbox.service import add_item
from app.domains.org.models import Employee, EmploymentStatus, OrgMembership
from app.domains.org.service import managed_scope
from app.domains.review.routing import active_delegations, can_review, review_assignment
from app.models import User


class BorrowExtensionCapacityConflict(HTTPException):
    def __init__(self) -> None:
        super().__init__(409, "借调无法按新日期安排，请联系原部门经理核对")


def extension_reviewer(
    principal: Principal,
    session: Session,
    employee_id: UUID,
    target_department_id: UUID,
) -> UUID | None:
    reviewer = can_review(session, principal, employee_id)
    if reviewer is None or extension_department_allowed(
        principal, session, target_department_id
    ):
        return reviewer
    return None


def extension_department_allowed(
    principal: Principal, session: Session, target_department_id: UUID
) -> bool:
    if represents_department(principal, target_department_id):
        return True
    target_ids = managed_scope(
        session, principal.org_id, frozenset({target_department_id})
    )
    member = session.exec(
        select(Employee.id).where(
            employee_scope_clause(principal),
            Employee.id == principal.employee_id,
            Employee.employment_status == EmploymentStatus.ACTIVE,
            col(Employee.department_id).in_(target_ids),
        )
    ).first()
    return member is not None


def extension_public(
    request: CapacityExtensionRequest,
    row: CapacityAssignment,
    demand: CapacityDemand,
    employee_name: str,
    *,
    anonymous: bool = False,
) -> ExtensionPublic:
    fields = request.model_dump()
    if anonymous:
        fields["reason"] = "借调员工申请调整完成日期"
    return ExtensionPublic(
        **fields,
        employee_name="借调 1 人" if anonymous else employee_name,
        demand_id=demand.id,
        capability_name=demand.capability_name,
        current_due_date=row.due_date,
        committed_end_date=demand.committed_end_date,
    )


def user_names(session: Session, org_id: UUID, user_ids: set[UUID]) -> dict[UUID, str]:
    return {
        user_id: employee_name or full_name or "企业管理员"
        for user_id, employee_name, full_name in session.exec(
            select(OrgMembership.user_id, Employee.display_name, User.full_name)
            .join(User, col(User.id) == OrgMembership.user_id)
            .outerjoin(
                Employee,
                (col(Employee.org_id) == OrgMembership.org_id)
                & (col(Employee.id) == OrgMembership.employee_id),
            )
            .where(
                OrgMembership.org_id == org_id, col(OrgMembership.user_id).in_(user_ids)
            )
            .order_by(col(User.id))
        ).all()
    }


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
    users = extension_reviewers(principal, session, row, demand)
    for user in users:
        add_item(
            session,
            InboxItem(
                org_id=principal.org_id,
                recipient_user_id=user.id,
                kind=InboxKind.CAPACITY,
                message=EXTENSION_REVIEW_NOTICE,
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
    result = extension_public(
        request, row, demand, employee(principal, session, row.employee_id).display_name
    )
    result.reviewer_names = list(
        user_names(session, principal.org_id, {u.id for u in users}).values()
    )
    return result


def extension_reviewers(
    principal: Principal,
    session: Session,
    row: CapacityAssignment,
    demand: CapacityDemand,
) -> list[User]:
    borrowed = assignment_lender(session, row) is not None
    if borrowed:
        recipients = responder_user_ids(
            session, row.org_id, demand.target_department_id
        )
    else:
        managers, _ = review_assignment(session, principal.org_id, row.employee_id)
        delegates = active_delegations(session, principal.org_id, managers)
        recipients = set(managers) | {d.delegate_user_id for d in delegates}
    users = session.exec(
        select(User)
        .join(OrgMembership, col(OrgMembership.user_id) == User.id)
        .where(
            OrgMembership.org_id == principal.org_id,
            col(User.id).in_(recipients),
            User.is_active,
        )
        .order_by(col(User.id))
    ).all()
    if borrowed:
        return list(users)
    return [
        user
        for user in users
        if (
            extension_reviewer(
                get_org_principal(principal.org_id, user, session),
                session,
                row.employee_id,
                demand.target_department_id,
            )
            is not None
        )
    ]


def list_extension_requests(principal: Principal, session: Session) -> ExtensionList:
    require_enabled(principal, session)
    query = (
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
            or_(
                CapacityExtensionRequest.status == CapacityRequestStatus.PENDING,
                CapacityExtensionRequest.decided_by_user_id == principal.user_id,
                CapacityExtensionRequest.decided_for_user_id == principal.user_id,
                CapacityAssignment.employee_id == principal.employee_id,
            ),
        )
        .order_by(
            col(CapacityExtensionRequest.created_at).desc(),
            col(CapacityExtensionRequest.id),
        )
    )
    rows = list(
        session.exec(
            query.where(
                CapacityExtensionRequest.status == CapacityRequestStatus.PENDING
            )
        ).all()
    )
    rows.extend(
        session.exec(
            query.where(
                CapacityExtensionRequest.status != CapacityRequestStatus.PENDING
            ).limit(100)
        ).all()
    )
    rows.sort(key=lambda item: (-item[0].created_at.timestamp(), item[0].id))
    reviewers: dict[UUID, UUID | None] = {}
    departments: dict[UUID, bool] = {}
    reviewer_names: dict[UUID, str] = {}
    confirmer_names: dict[UUID, list[str]] = {}
    data = []
    for request, row, demand, name in rows:
        borrowed = assignment_lender(session, row) is not None
        if borrowed and row.employee_id != principal.employee_id:
            if represents_department(principal, demand.target_department_id):
                data.append(
                    extension_public(request, row, demand, name, anonymous=True)
                )
            continue
        result = extension_public(request, row, demand, name)
        if row.employee_id != principal.employee_id:
            if row.employee_id not in reviewers:
                reviewers[row.employee_id] = can_review(
                    session, principal, row.employee_id
                )
            if reviewers[row.employee_id] is None:
                continue
            target = demand.target_department_id
            if target not in departments:
                departments[target] = extension_department_allowed(
                    principal, session, target
                )
            if not departments[target]:
                continue
            reviewer = reviewers[row.employee_id]
            assert reviewer is not None
            if reviewer != principal.user_id:
                if reviewer not in reviewer_names:
                    reviewer_names[reviewer] = user_names(
                        session, principal.org_id, {reviewer}
                    )[reviewer]
                result.on_behalf_of_name = reviewer_names[reviewer]
            if (
                request.status == CapacityRequestStatus.PENDING
                and demand.committed_end_date is not None
                and request.requested_due_date > demand.committed_end_date
                and not represents_department(principal, target)
            ):
                if target not in confirmer_names:
                    confirmer_names[target] = list(
                        user_names(
                            session,
                            principal.org_id,
                            responder_user_ids(session, principal.org_id, target),
                        ).values()
                    )
                result.commitment_confirmer_names = confirmer_names[target]
        data.append(result)
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
    row, demand = assignment_rows(
        principal, session, request.assignment_id, allow_borrow=True
    )
    session.refresh(request, with_for_update=True)
    lender = assignment_lender(session, row)
    manager_id = (
        extension_reviewer(
            principal, session, row.employee_id, demand.target_department_id
        )
        if lender is None
        else principal.user_id
        if principal.employee_id != row.employee_id
        and represents_department(principal, demand.target_department_id)
        else None
    )
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
        try:
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
                lender_department_id=lender,
            )
        except AssignmentCapacityConflict as error:
            if lender is not None:
                raise BorrowExtensionCapacityConflict() from error
            raise
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
    if lender is not None:
        return extension_public(request, row, demand, "", anonymous=True)
    return extension_public(
        request, row, demand, employee(principal, session, row.employee_id).display_name
    )
