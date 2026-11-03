from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException
from sqlmodel import Session, col, select

from app.api.deps import CurrentUser, SessionDep
from app.authz.principal import Principal
from app.domains.audit.models import ActorKind
from app.domains.org.models import Department, OrgMembership, Role, RoleGrant


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
