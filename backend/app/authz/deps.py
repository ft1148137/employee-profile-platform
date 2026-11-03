from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException
from sqlmodel import select

from app.api.deps import CurrentUser, SessionDep
from app.authz.principal import Principal
from app.domains.audit.models import ActorKind
from app.domains.org.models import OrgMembership, Role, RoleGrant
from app.domains.org.service import managed_scope as managed_scope


def get_org_principal(
    org_id: UUID, current_user: CurrentUser, session: SessionDep
) -> Principal:
    membership = session.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == org_id, OrgMembership.user_id == current_user.id
        )
    ).first()
    if membership is None:
        raise HTTPException(status_code=404, detail="未找到该企业或你没有权限访问")
    grants = session.exec(
        select(RoleGrant).where(
            RoleGrant.org_id == org_id, RoleGrant.membership_id == membership.id
        )
    ).all()
    return Principal(
        ActorKind.USER,
        org_id,
        current_user.id,
        membership.employee_id,
        any(g.role == Role.ORG_ADMIN for g in grants),
        managed_scope(
            session,
            org_id,
            frozenset(
                g.department_id
                for g in grants
                if g.role == Role.DEPT_MANAGER and g.department_id is not None
            ),
        ),
    )


OrgPrincipalDep = Annotated[Principal, Depends(get_org_principal)]


class OrgAdminRequired(HTTPException):
    def __init__(self) -> None:
        super().__init__(403, "需要企业管理员权限")


def get_org_admin(principal: OrgPrincipalDep) -> Principal:
    if not principal.is_org_admin:
        raise OrgAdminRequired()
    return principal


OrgAdminDep = Annotated[Principal, Depends(get_org_admin)]
