from uuid import UUID

from sqlmodel import Session, col, select

from app.domains.change_request.models import ProfileChangeRequest
from app.domains.inbox.models import InboxKind
from app.domains.inbox.service import notify
from app.domains.org.models import (
    Department,
    Employee,
    OrgMembership,
    ReportingLine,
    Role,
    RoleGrant,
)
from app.domains.org.service import managed_scope


def reviewers_for(
    session: Session, org_id: UUID, subject_employee_id: UUID
) -> list[OrgMembership]:
    subject = session.exec(
        select(Employee).where(
            Employee.org_id == org_id, Employee.id == subject_employee_id
        )
    ).one_or_none()
    if subject is None:
        return []
    rows = session.exec(
        select(OrgMembership, RoleGrant)
        .join(
            RoleGrant,
            (col(RoleGrant.membership_id) == OrgMembership.id)
            & (col(RoleGrant.org_id) == OrgMembership.org_id),
        )
        .where(OrgMembership.org_id == org_id)
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
                return [member]
    parents = dict(
        session.exec(
            select(Department.id, Department.parent_department_id).where(
                Department.org_id == org_id
            )
        ).all()
    )
    department, visited = subject.department_id, set()
    while department is not None and department not in visited:
        visited.add(department)
        managers = {
            m.id: m
            for m, g in candidates
            if g.role == Role.DEPT_MANAGER and g.department_id == department
        }
        if managers:
            return list(managers.values())
        department = parents.get(department)
    return list({m.id: m for m, g in candidates if g.role == Role.ORG_ADMIN}.values())


def notify_reviewers(session: Session, request: ProfileChangeRequest) -> None:
    for member in reviewers_for(session, request.org_id, request.subject_employee_id):
        if member.employee_id is not None:
            notify(
                session,
                request,
                InboxKind.REVIEW_REQUESTED,
                "有新的画像请求待审批",
                recipient_employee_id=member.employee_id,
            )
