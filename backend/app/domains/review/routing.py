from collections import defaultdict
from collections.abc import Collection
from uuid import UUID

from sqlmodel import Session, SQLModel, col, or_, select

from app.authz.principal import Principal
from app.domains.base import utc_now
from app.domains.change_request.models import ProfileChangeRequest
from app.domains.change_request.schemas import RequestReviewer
from app.domains.inbox.models import InboxKind
from app.domains.inbox.service import notify
from app.domains.org.models import (
    Department,
    Employee,
    EmploymentStatus,
    OrgMembership,
    ReportingLine,
    Role,
    RoleGrant,
)
from app.domains.org.service import department_ancestry
from app.domains.review.models import ReviewDelegation
from app.models import User


class DirectReport(SQLModel):
    employee_name: str
    employee_code: str | None
    reviewers: list[RequestReviewer]
    is_reviewer: bool


def direct_reports(session: Session, principal: Principal) -> list[DirectReport]:
    from app.domains.change_request.service import request_reviewers

    if principal.employee_id is None:
        return []
    reports = session.exec(
        select(Employee)
        .join(
            ReportingLine,
            (col(ReportingLine.org_id) == Employee.org_id)
            & (col(ReportingLine.employee_id) == Employee.id),
        )
        .where(
            Employee.org_id == principal.org_id,
            Employee.employment_status == EmploymentStatus.ACTIVE,
            ReportingLine.manager_employee_id == principal.employee_id,
            col(ReportingLine.valid_to).is_(None),
        )
        .order_by(Employee.display_name, col(Employee.employee_code), col(Employee.id))
    ).all()
    return [
        DirectReport(
            employee_name=report.display_name,
            employee_code=report.employee_code,
            reviewers=request_reviewers(session, principal.org_id, report.id),
            is_reviewer=can_review(session, principal, report.id) is not None,
        )
        for report in reports
    ]


def reviewers_for(
    session: Session, org_id: UUID, subject_employee_id: UUID
) -> list[UUID]:
    return review_assignment(session, org_id, subject_employee_id)[0]


def review_assignment(
    session: Session, org_id: UUID, subject_employee_id: UUID
) -> tuple[list[UUID], str | None]:
    return _review_assignments(session, org_id, {subject_employee_id}).get(
        subject_employee_id, ([], None)
    )


def _review_assignments(
    session: Session, org_id: UUID, subject_employee_ids: Collection[UUID]
) -> dict[UUID, tuple[list[UUID], str | None]]:
    if not subject_employee_ids:
        return {}
    subjects = session.exec(
        select(Employee.id, Employee.department_id).where(
            Employee.org_id == org_id, col(Employee.id).in_(subject_employee_ids)
        )
    ).all()
    if not subjects:
        return {}
    rows = session.exec(
        select(
            OrgMembership.user_id,
            OrgMembership.employee_id,
            RoleGrant.role,
            RoleGrant.department_id,
        )
        .join(
            RoleGrant,
            (col(RoleGrant.membership_id) == OrgMembership.id)
            & (col(RoleGrant.org_id) == OrgMembership.org_id),
        )
        .join(User, col(User.id) == OrgMembership.user_id)
        .outerjoin(
            Employee,
            (col(Employee.id) == OrgMembership.employee_id)
            & (col(Employee.org_id) == OrgMembership.org_id),
        )
        .where(
            OrgMembership.org_id == org_id,
            User.is_active,
            or_(
                col(OrgMembership.employee_id).is_(None),
                Employee.employment_status == EmploymentStatus.ACTIVE,
            ),
        )
    ).all()
    admins: dict[UUID, UUID | None] = {}
    managers: dict[UUID, dict[UUID, UUID | None]] = defaultdict(dict)
    for user_id, employee_id, role, department_id in rows:
        if role == Role.ORG_ADMIN:
            admins[user_id] = employee_id
        elif role == Role.DEPT_MANAGER and department_id is not None:
            managers[department_id][user_id] = employee_id
    reporting_lines = dict(
        session.exec(
            select(ReportingLine.employee_id, ReportingLine.manager_employee_id).where(
                ReportingLine.org_id == org_id,
                col(ReportingLine.employee_id).in_(
                    [employee_id for employee_id, _ in subjects]
                ),
                col(ReportingLine.valid_to).is_(None),
            )
        ).all()
    )
    departments = {
        row.id: row
        for row in session.exec(
            select(Department).where(Department.org_id == org_id)
        ).all()
    }
    return {
        employee_id: _assignment(
            employee_id,
            department_id,
            reporting_lines.get(employee_id),
            departments,
            managers,
            admins,
        )
        for employee_id, department_id in subjects
    }


def _assignment(
    employee_id: UUID,
    department_id: UUID | None,
    direct_manager_id: UUID | None,
    departments: dict[UUID, Department],
    managers: dict[UUID, dict[UUID, UUID | None]],
    admins: dict[UUID, UUID | None],
) -> tuple[list[UUID], str]:
    ancestry = list(department_ancestry(departments, department_id))
    if direct_manager_id is not None:
        for ancestor in ancestry:
            for user_id, manager_id in managers.get(ancestor, {}).items():
                if manager_id == direct_manager_id and manager_id != employee_id:
                    return [user_id], "申请人的直属上级，并且是该部门的部门经理"
    for ancestor in ancestry:
        reviewers = sorted(
            user_id
            for user_id, manager_id in managers.get(ancestor, {}).items()
            if manager_id != employee_id
        )
        if reviewers:
            reason = (
                "申请人所在部门的部门经理"
                if ancestor == department_id
                else f"申请人上级部门 {departments[ancestor].name} 的部门经理"
            )
            return reviewers, reason
    return (
        sorted(
            user_id for user_id, admin_id in admins.items() if admin_id != employee_id
        ),
        "申请人所在部门和上级部门都没有部门经理，由企业管理员审批",
    )


def active_delegations(
    session: Session, org_id: UUID, managers: Collection[UUID]
) -> list[ReviewDelegation]:
    if not managers:
        return []
    now = utc_now()
    return list(
        session.exec(
            select(ReviewDelegation)
            .join(
                OrgMembership,
                (col(OrgMembership.org_id) == ReviewDelegation.org_id)
                & (col(OrgMembership.user_id) == ReviewDelegation.delegate_user_id),
            )
            .join(User, col(User.id) == OrgMembership.user_id)
            .where(
                ReviewDelegation.org_id == org_id,
                col(ReviewDelegation.manager_user_id).in_(managers),
                col(ReviewDelegation.revoked_at).is_(None),
                ReviewDelegation.starts_at <= now,
                ReviewDelegation.ends_at > now,
                User.is_active,
            )
            .order_by(col(ReviewDelegation.manager_user_id))
            .with_for_update(read=True, of=ReviewDelegation)
        ).all()
    )


def can_review(
    session: Session, principal: Principal, subject_employee_id: UUID
) -> UUID | None:
    """Return the original reviewer represented by this actor, without following chains."""
    return reviewable_employees(session, principal, {subject_employee_id}).get(
        subject_employee_id
    )


def reviewable_employees(
    session: Session, principal: Principal, subject_employee_ids: Collection[UUID]
) -> dict[UUID, UUID | None]:
    user_id = principal.user_id
    if user_id is None:
        return {}
    assignments = _review_assignments(
        session, principal.org_id, set(subject_employee_ids) - {principal.employee_id}
    )
    delegated_managers = {
        row.manager_user_id
        for row in active_delegations(
            session,
            principal.org_id,
            {
                manager_id
                for reviewers, _ in assignments.values()
                if user_id not in reviewers
                for manager_id in reviewers
            },
        )
        if row.delegate_user_id == user_id
    }
    return {
        employee_id: _represented_reviewer(user_id, reviewers, delegated_managers)
        for employee_id, (reviewers, _) in assignments.items()
    }


def _represented_reviewer(
    user_id: UUID, reviewers: list[UUID], delegated_managers: set[UUID]
) -> UUID | None:
    if user_id in reviewers:
        return user_id
    return next(
        (manager for manager in reviewers if manager in delegated_managers), None
    )


def notify_reviewers(session: Session, request: ProfileChangeRequest) -> None:
    reviewers = reviewers_for(session, request.org_id, request.subject_employee_id)
    delegates = active_delegations(session, request.org_id, reviewers)
    recipients = set(reviewers) | {row.delegate_user_id for row in delegates}
    for user_id in sorted(recipients):
        member = session.exec(
            select(OrgMembership).where(
                OrgMembership.org_id == request.org_id, OrgMembership.user_id == user_id
            )
        ).one()
        if member.employee_id == request.subject_employee_id:
            continue
        notify(
            session,
            request,
            InboxKind.REVIEW_REQUESTED,
            "有新的画像请求待审批",
            recipient_user_id=user_id,
        )
