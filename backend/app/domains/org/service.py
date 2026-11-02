from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func
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
    statement = select(Employee).where(employee_scope_clause(principal))
    if department_id is not None:
        statement = statement.where(Employee.department_id == department_id)
    if q:
        statement = statement.where(
            col(Employee.display_name).icontains(q, autoescape=True)
        )
    count = session.exec(select(func.count()).select_from(statement.subquery())).one()
    employees = session.exec(
        statement.order_by(Employee.display_name, col(Employee.id))
        .offset(skip)
        .limit(limit)
    ).all()
    return EmployeesPublic(
        data=[employee_summary(session, e) for e in employees], count=count
    )
