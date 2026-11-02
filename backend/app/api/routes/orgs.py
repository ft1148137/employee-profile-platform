from fastapi import APIRouter
from sqlmodel import col, select

from app.api.deps import SessionDep
from app.authz.deps import OrgAdminDep, OrgPrincipalDep
from app.domains.audit.service import record_audit
from app.domains.org.models import Department, Organization
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
    organization = session.exec(
        select(Organization)
        .where(Organization.id == principal.org_id)
        .with_for_update()
    ).one()
    old_name = organization.name
    organization.name = body.name
    session.add(organization)
    record_audit(
        session,
        org_id=principal.org_id,
        actor_type=principal.actor_type,
        actor_id=str(principal.user_id),
        action="org.renamed",
        target_type="organization",
        target_id=str(principal.org_id),
        details={"old_name": old_name, "new_name": body.name},
    )
    session.commit()
    session.refresh(organization)
    return OrganizationPublic.model_validate(organization)


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
