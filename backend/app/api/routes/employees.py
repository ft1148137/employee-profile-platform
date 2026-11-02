from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlmodel import select

from app.api.deps import SessionDep
from app.authz.deps import OrgPrincipalDep
from app.authz.policies import employee_scope_clause
from app.domains.org.models import Employee
from app.domains.org.schemas import EmployeesPublic, EmployeeSummary
from app.domains.org.service import employee_summary, list_employees

router = APIRouter(prefix="/orgs/{org_id}/employees", tags=["employees"])


@router.get("", response_model=EmployeesPublic)
def read_employees(
    session: SessionDep,
    principal: OrgPrincipalDep,
    department_id: UUID | None = None,
    q: str | None = None,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
) -> EmployeesPublic:
    return list_employees(
        session, principal, department_id=department_id, q=q, skip=skip, limit=limit
    )


@router.get("/{employee_id}", response_model=EmployeeSummary)
def read_employee(
    employee_id: UUID, session: SessionDep, principal: OrgPrincipalDep
) -> EmployeeSummary:
    employee = session.exec(
        select(Employee).where(
            employee_scope_clause(principal), Employee.id == employee_id
        )
    ).first()
    if employee is None:
        raise HTTPException(status_code=404, detail="未找到该员工或你没有权限查看")
    return employee_summary(session, employee)
