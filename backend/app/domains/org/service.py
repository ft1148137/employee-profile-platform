from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import aliased
from sqlmodel import Session, col, select

from app.authz.policies import employee_scope_clause
from app.authz.principal import Principal
from app.domains.org.models import Department, Employee
from app.domains.org.schemas import (
    DepartmentSummary,
    EmployeesPublic,
    EmployeeSummary,
    ProgressPublic,
    PublishedSummary,
)
from app.domains.profile.models import ProfileProgress, ProfileVersion


class EmployeeNotFound(HTTPException):
    def __init__(self) -> None:
        super().__init__(404, "Employee not found")


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


def employee_summary(session: Session, employee: Employee) -> EmployeeSummary:
    department = session.exec(
        select(Department).where(
            Department.org_id == employee.org_id,
            Department.id == employee.department_id,
        )
    ).first()
    progress = session.exec(
        select(ProfileProgress).where(
            ProfileProgress.org_id == employee.org_id,
            ProfileProgress.employee_id == employee.id,
        )
    ).first()
    version = session.exec(
        select(ProfileVersion)
        .where(
            ProfileVersion.org_id == employee.org_id,
            ProfileVersion.subject_employee_id == employee.id,
        )
        .order_by(col(ProfileVersion.revision).desc())
        .limit(1)
    ).first()
    return _employee_summary(employee, department, progress, version)


def _employee_summary(
    employee: Employee,
    department: Department | None,
    progress: ProfileProgress | None,
    version: ProfileVersion | None,
) -> EmployeeSummary:
    return EmployeeSummary(
        **employee.model_dump(
            include={"id", "display_name", "title", "employment_status"}
        ),
        department=DepartmentSummary.model_validate(department) if department else None,
        progress=ProgressPublic.model_validate(progress) if progress else None,
        latest_published=PublishedSummary.model_validate(version) if version else None,
    )


def list_employees(
    session: Session,
    principal: Principal,
    *,
    department_id: UUID | None,
    q: str | None,
    skip: int,
    limit: int,
) -> EmployeesPublic:
    filters = [employee_scope_clause(principal)]
    if department_id is not None:
        filters.append(col(Employee.department_id) == department_id)
    if q:
        filters.append(col(Employee.display_name).icontains(q, autoescape=True))
    count = session.exec(
        select(func.count()).select_from(Employee).where(*filters)
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
    rows = session.exec(
        select(Employee, Department, ProfileProgress, latest_version)
        .where(*filters)
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
    return EmployeesPublic(data=[_employee_summary(*row) for row in rows], count=count)
