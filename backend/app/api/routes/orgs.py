from fastapi import APIRouter
from sqlmodel import col, select

from app.api.deps import SessionDep
from app.authz.deps import OrgAdminDep, OrgPrincipalDep
from app.domains.org import service
from app.domains.org.employee_fields import EmployeeFields, save_employee_fields
from app.domains.org.models import Department
from app.domains.org.schemas import (
    DepartmentPublic,
    DepartmentsPublic,
    OrganizationPublic,
    OrganizationUpdate,
)

router = APIRouter(prefix="/orgs/{org_id}", tags=["orgs"])


@router.patch("", response_model=OrganizationPublic)
def rename_organization(
    body: OrganizationUpdate, session: SessionDep, principal: OrgAdminDep
) -> OrganizationPublic:
    return service.rename_organization(session, principal, body.name)


@router.get("/departments", response_model=DepartmentsPublic)
def read_departments(
    session: SessionDep, principal: OrgPrincipalDep
) -> DepartmentsPublic:
    departments = session.exec(
        select(Department)
        .where(Department.org_id == principal.org_id)
        .order_by(Department.name, col(Department.id))
    ).all()
    return DepartmentsPublic(
        data=[DepartmentPublic.model_validate(d) for d in departments],
        count=len(departments),
    )


@router.put("/employee-fields", response_model=EmployeeFields)
def update_employee_fields(
    body: EmployeeFields, session: SessionDep, principal: OrgAdminDep
) -> EmployeeFields:
    return save_employee_fields(session, principal, body)
