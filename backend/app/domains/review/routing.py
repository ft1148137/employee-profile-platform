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
from app.domains.org.service import managed_scope
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
    subject = session.exec(
        select(Employee).where(
            Employee.org_id == org_id, Employee.id == subject_employee_id
        )
    ).one_or_none()
    if subject is None:
        return [], None
    rows = session.exec(
        select(OrgMembership, RoleGrant)
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
    candidates = [(m, g) for m, g in rows if m.employee_id != subject.id]
    line = session.exec(
        select(ReportingLine).where(
            ReportingLine.org_id == org_id,
            ReportingLine.employee_id == subject.id,
            col(ReportingLine.valid_to).is_(None),
        )
    ).one_or_none()
    if line:
        for member, grant in candidates:
            if (
                member.employee_id == line.manager_employee_id
                and grant.role == Role.DEPT_MANAGER
                and grant.department_id
                and subject.department_id
                in managed_scope(session, org_id, frozenset([grant.department_id]))
            ):
                return [member.user_id], "申请人的直属上级，并且是该部门的部门经理"
    departments = {
        row.id: row
        for row in session.exec(
            select(Department).where(Department.org_id == org_id)
        ).all()
    }
    department, visited = subject.department_id, set()
    while department is not None and department not in visited:
        visited.add(department)
        managers = {
            m.user_id
            for m, g in candidates
            if g.role == Role.DEPT_MANAGER and g.department_id == department
        }
        if managers:
            reason = (
                "申请人所在部门的部门经理"
                if department == subject.department_id
                else f"申请人上级部门 {departments[department].name} 的部门经理"
            )
            return sorted(managers), reason
        row = departments.get(department)
        department = row.parent_department_id if row else None
    return (
        sorted({m.user_id for m, g in candidates if g.role == Role.ORG_ADMIN}),
        "申请人所在部门和上级部门都没有部门经理，由企业管理员审批",
    )


def active_delegations(
    session: Session, org_id: UUID, managers: list[UUID]
) -> list[ReviewDelegation]:
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
    if principal.user_id is None or principal.employee_id == subject_employee_id:
        return None
    reviewers = reviewers_for(session, principal.org_id, subject_employee_id)
    if principal.user_id in reviewers:
        return principal.user_id
    return next(
        (
            row.manager_user_id
            for row in active_delegations(session, principal.org_id, reviewers)
            if row.delegate_user_id == principal.user_id
        ),
        None,
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
