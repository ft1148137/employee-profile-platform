from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import aliased
from sqlalchemy.sql.selectable import Subquery
from sqlmodel import Session, col, select
from sqlmodel.sql.expression import Select

from app.authz.policies import employee_scope_clause
from app.authz.principal import Principal
from app.domains.audit.service import record_audit
from app.domains.change_request.service import review_statuses
from app.domains.org.models import (
    Department,
    Employee,
    EmployeeField,
    EmploymentStatus,
    Organization,
    OrgMembership,
    ReportingLine,
    Role,
    RoleGrant,
)
from app.domains.org.schemas import (
    DepartmentManager,
    DepartmentSummary,
    EmployeeReference,
    EmployeesPublic,
    EmployeeSummary,
    OrganizationPublic,
    OrgChartDepartment,
    OrgChartPublic,
    ProgressPublic,
    PublishedSummary,
)
from app.domains.profile.models import (
    ProfileProgress,
    ProfileVersion,
    ProgressReviewStatus,
)
from app.models import User


class OrgChartForbidden(HTTPException):
    def __init__(self) -> None:
        super().__init__(403, "需要企业管理员或部门经理权限")


def read_org_chart(session: Session, principal: Principal) -> OrgChartPublic:
    if not principal.is_org_admin and not principal.managed_department_ids:
        raise OrgChartForbidden()
    departments = session.exec(
        select(Department)
        .where(
            Department.org_id == principal.org_id,
            True
            if principal.is_org_admin
            else col(Department.id).in_(principal.managed_department_ids),
        )
        .order_by(Department.name, col(Department.id))
    ).all()
    nodes = {d.id: OrgChartDepartment.model_validate(d) for d in departments}
    for node in nodes.values():
        if node.parent_department_id not in nodes:
            node.parent_department_id = None
        ancestor = node.parent_department_id
        seen = {node.id}
        while ancestor is not None and ancestor in nodes:
            if ancestor in seen:
                node.parent_department_id = None
                break
            seen.add(ancestor)
            ancestor = nodes[ancestor].parent_department_id
    counts = session.exec(
        select(Employee.department_id, func.count())
        .where(
            employee_scope_clause(principal),
            Employee.employment_status == EmploymentStatus.ACTIVE,
        )
        .group_by(col(Employee.department_id))
    ).all()
    for department_id, count in counts:
        if department_id in nodes:
            nodes[department_id].direct_count = count
    for node in nodes.values():
        ancestor = node.id
        seen = set()
        while ancestor is not None and ancestor in nodes and ancestor not in seen:
            seen.add(ancestor)
            nodes[ancestor].total_count += node.direct_count
            ancestor = nodes[ancestor].parent_department_id
    managers = session.exec(
        select(RoleGrant.department_id, Employee, User)
        .join(
            OrgMembership,
            (col(OrgMembership.id) == RoleGrant.membership_id)
            & (OrgMembership.org_id == principal.org_id),
        )
        .join(User, col(User.id) == OrgMembership.user_id)
        .outerjoin(
            Employee,
            (col(Employee.id) == OrgMembership.employee_id)
            & (Employee.org_id == principal.org_id),
        )
        .where(
            RoleGrant.org_id == principal.org_id,
            RoleGrant.role == Role.DEPT_MANAGER,
            col(RoleGrant.department_id).in_(nodes),
        )
        .order_by(User.username, col(User.id))
    ).all()
    for department_id, employee, user in managers:
        if department_id in nodes:
            nodes[department_id].managers.append(
                DepartmentManager(
                    employee_id=employee.id if employee else None,
                    display_name=employee.display_name if employee else user.username,
                )
            )
    return OrgChartPublic(departments=list(nodes.values()))


def _reporting_managers(principal: Principal) -> Subquery:
    return (
        select(
            ReportingLine.employee_id,
            col(Employee.id).label("id"),
            Employee.display_name,
        )
        .join(
            Employee,
            (col(Employee.id) == ReportingLine.manager_employee_id)
            & (Employee.org_id == ReportingLine.org_id),
        )
        .where(
            ReportingLine.org_id == principal.org_id,
            col(ReportingLine.valid_to).is_(None),
            employee_scope_clause(principal),
        )
        .subquery()
    )


def managed_scope(
    session: Session, org_id: UUID, department_ids: frozenset[UUID]
) -> frozenset[UUID]:
    if not department_ids:
        return frozenset()
    scope = (
        select(Department.id)
        .where(Department.org_id == org_id, col(Department.id).in_(department_ids))
        .cte("managed_scope", recursive=True)
    )
    # UNION deduplicates overlapping grants and terminates even with a hierarchy cycle.
    scope = scope.union(
        select(Department.id)
        .join(scope, col(Department.parent_department_id) == scope.c.id)
        .where(Department.org_id == org_id)
    )
    return frozenset(session.exec(select(scope.c.id)).all())


class EmployeeNotFound(HTTPException):
    def __init__(self) -> None:
        super().__init__(404, "未找到该员工或你没有权限查看")


def visible_employee(
    session: Session, principal: Principal, employee_id: UUID
) -> Employee:
    employee = session.exec(
        select(Employee).where(
            employee_scope_clause(principal), Employee.id == employee_id
        )
    ).first()
    if employee is None:
        raise EmployeeNotFound()
    return employee


def employee_summary(
    session: Session, employee: Employee, principal: Principal
) -> EmployeeSummary:
    managers = _reporting_managers(principal)
    department, progress, version, manager_id, manager_name, fields = session.exec(
        Select[
            tuple[
                Department | None,
                ProfileProgress | None,
                ProfileVersion | None,
                UUID | None,
                str | None,
                list[EmployeeField],
            ]
        ](
            Department,
            ProfileProgress,
            ProfileVersion,
            managers.c.id,
            managers.c.display_name,
            col(Organization.employee_fields),
        )
        .select_from(Employee)
        .join(Organization, col(Organization.id) == Employee.org_id)
        .outerjoin(managers, col(Employee.id) == managers.c.employee_id)
        .outerjoin(
            Department,
            (col(Department.org_id) == Employee.org_id)
            & (col(Department.id) == Employee.department_id),
        )
        .outerjoin(
            ProfileProgress,
            (col(ProfileProgress.org_id) == Employee.org_id)
            & (col(ProfileProgress.employee_id) == Employee.id),
        )
        .outerjoin(
            ProfileVersion,
            (col(ProfileVersion.org_id) == Employee.org_id)
            & (col(ProfileVersion.subject_employee_id) == Employee.id),
        )
        .where(col(Employee.org_id) == employee.org_id, col(Employee.id) == employee.id)
        .order_by(col(ProfileVersion.revision).desc())
        .limit(1)
    ).one()
    return _employee_summary(
        employee,
        department,
        progress,
        version,
        fields,
        review_statuses(
            session, employee.org_id, [employee.id], {employee.id} if version else set()
        )[employee.id],
        manager_id,
        manager_name,
    )


def _employee_summary(
    employee: Employee,
    department: Department | None,
    progress: ProfileProgress | None,
    version: ProfileVersion | None,
    fields: list[EmployeeField],
    review_status: ProgressReviewStatus,
    manager_id: UUID | None,
    manager_name: str | None,
) -> EmployeeSummary:
    return EmployeeSummary(
        **{f: getattr(employee, f) for f in fields},
        **employee.model_dump(
            include={"id", "display_name", "title", "employment_status"}
        ),
        department=DepartmentSummary.model_validate(department) if department else None,
        progress=ProgressPublic(
            **(
                progress
                or ProfileProgress(org_id=employee.org_id, employee_id=employee.id)
            ).model_dump(),
            review_status=review_status,
        ),
        latest_published=PublishedSummary.model_validate(version) if version else None,
        manager=EmployeeReference(id=manager_id, display_name=manager_name)
        if manager_id and manager_name is not None
        else None,
    )


def list_employees(
    session: Session,
    principal: Principal,
    *,
    department_id: UUID | None,
    q: str | None,
    skip: int,
    limit: int,
    employment_status: EmploymentStatus | None = None,
) -> EmployeesPublic:
    filters = [employee_scope_clause(principal)]
    if employment_status is not None:
        filters.append(col(Employee.employment_status) == employment_status)
    if department_id is not None:
        filters.append(col(Employee.department_id) == department_id)
    if q:
        filters.append(col(Employee.display_name).icontains(q, autoescape=True))
    count, fields = session.exec(
        select(
            func.count(),
            select(col(Organization.employee_fields))
            .where(Organization.id == principal.org_id)
            .scalar_subquery(),
        )
        .select_from(Employee)
        .where(*filters)
    ).one()
    latest_version = aliased(
        ProfileVersion,
        select(ProfileVersion)
        .where(ProfileVersion.org_id == principal.org_id)
        .distinct(col(ProfileVersion.subject_employee_id))
        .order_by(
            col(ProfileVersion.subject_employee_id), col(ProfileVersion.revision).desc()
        )
        .subquery(),
    )
    managers = _reporting_managers(principal)
    rows = session.exec(
        Select[
            tuple[
                Employee,
                Department | None,
                ProfileProgress | None,
                ProfileVersion | None,
                UUID | None,
                str | None,
            ]
        ](
            Employee,
            Department,
            ProfileProgress,
            latest_version,
            managers.c.id,
            managers.c.display_name,
        )
        .where(*filters)
        .outerjoin(managers, col(Employee.id) == managers.c.employee_id)
        .outerjoin(
            Department,
            (col(Department.org_id) == Employee.org_id)
            & (col(Department.id) == Employee.department_id),
        )
        .outerjoin(
            ProfileProgress,
            (col(ProfileProgress.org_id) == Employee.org_id)
            & (col(ProfileProgress.employee_id) == Employee.id),
        )
        .outerjoin(
            latest_version,
            (col(latest_version.org_id) == Employee.org_id)
            & (col(latest_version.subject_employee_id) == Employee.id),
        )
        .order_by(Employee.display_name, col(Employee.id))
        .offset(skip)
        .limit(limit)
    ).all()
    statuses = review_statuses(
        session,
        principal.org_id,
        [e.id for e, *_ in rows],
        {e.id for e, _, _, version, *_ in rows if version},
    )
    return EmployeesPublic(
        data=[
            _employee_summary(
                e, d, p, v, fields, statuses[e.id], manager_id, manager_name
            )
            for e, d, p, v, manager_id, manager_name in rows
        ],
        count=count,
    )


def rename_organization(
    session: Session, principal: Principal, name: str, *, commit: bool = True
) -> OrganizationPublic:
    organization = session.exec(
        select(Organization)
        .where(Organization.id == principal.org_id)
        .with_for_update()
    ).one()
    old_name = organization.name
    organization.name = name
    session.add(organization)
    record_audit(
        session,
        org_id=principal.org_id,
        actor_type=principal.actor_type,
        actor_id=str(principal.user_id),
        action="org.renamed",
        target_type="organization",
        target_id=str(principal.org_id),
        details={"old_name": old_name, "new_name": name},
    )
    if commit:
        session.commit()
        session.refresh(organization)
    else:
        session.flush()
    return OrganizationPublic.model_validate(organization)
