from datetime import date
from typing import Literal
from uuid import UUID

from sqlmodel import Session, col, or_, select

from app.authz.policies import employee_scope_clause
from app.authz.principal import Principal
from app.domains.capability.search import PeopleSearchInput, find_people
from app.domains.capability.service import get_schema, list_capabilities
from app.domains.capacity.demand_schemas import (
    CapacityReference,
    DemandEventPublic,
    DemandInput,
    DemandList,
    DemandPublic,
)
from app.domains.capacity.models import (
    CapacityDemand,
    CapacityDemandEvent,
    DemandEventKind,
    RequesterPolicy,
)
from app.domains.capacity.schemas import Via
from app.domains.capacity.settings import (
    CapacityForbidden,
    CapacityNotFound,
    audit,
    read_settings,
    require_enabled,
)
from app.domains.capacity.workload import NOTICE, employee, views
from app.domains.inbox.models import InboxItem, InboxKind
from app.domains.inbox.service import add_item
from app.domains.org.models import (
    Department,
    Employee,
    EmploymentStatus,
    OrgMembership,
    Role,
    RoleGrant,
)
from app.domains.org.schemas import DepartmentPublic, DepartmentsPublic
from app.models import User


def require_requester(principal: Principal, session: Session) -> None:
    require_enabled(principal, session)
    if principal.user_id is None:
        raise CapacityForbidden()
    if principal.is_org_admin or principal.managed_department_ids:
        return
    if read_settings(principal, session).requester_policy != RequesterPolicy.EVERYONE:
        raise CapacityForbidden()
    employee(principal, session, principal.employee_id)


def department(session: Session, org_id: UUID, department_id: UUID) -> Department:
    row = session.exec(
        select(Department).where(
            Department.org_id == org_id, Department.id == department_id
        )
    ).one_or_none()
    if row is None:
        raise CapacityNotFound("未找到该部门")
    return row


def list_capacity_departments(
    principal: Principal, session: Session
) -> DepartmentsPublic:
    require_requester(principal, session)
    rows = session.exec(
        select(Department)
        .where(Department.org_id == principal.org_id)
        .order_by(Department.name, col(Department.id))
    ).all()
    return DepartmentsPublic(
        data=[DepartmentPublic.model_validate(d) for d in rows], count=len(rows)
    )


def department_reference(
    session: Session,
    org_id: UUID,
    target_department_id: UUID,
    capability_key: str,
    minimum_level: str | None = None,
) -> CapacityReference:
    target = department(session, org_id, target_department_id)
    worker = Principal.system_worker(org_id)
    ids: set[UUID] = set()
    skip = 0
    while True:
        page = find_people(
            session,
            worker,
            PeopleSearchInput(
                department_id=target.id,
                capability_key=capability_key,
                minimum_level=minimum_level,
                skip=skip,
                limit=100,
            ),
        )
        ids.update(r.employee_id for r in page.data if r.level is not None)
        skip += len(page.data)
        if skip >= page.count or not page.data:
            break
    capability = next(
        c
        for c in list_capabilities(session, org_id, get_schema(session, org_id).id)
        if c.key == capability_key
    )
    result = CapacityReference(
        department_name=target.name, capability_name=capability.name, count=len(ids)
    )
    employees = list(
        session.exec(
            select(Employee).where(
                employee_scope_clause(worker),
                col(Employee.id).in_(ids),
            )
        ).all()
    )
    for workload in views(worker, session, employees):
        setattr(result, workload.band, getattr(result, workload.band) + 1)
    return result


def capacity_reference(
    principal: Principal,
    session: Session,
    target_department_id: UUID,
    capability_key: str,
    minimum_level: str | None = None,
) -> CapacityReference:
    require_requester(principal, session)
    return department_reference(
        session, principal.org_id, target_department_id, capability_key, minimum_level
    )


def represents_department(principal: Principal, target_department_id: UUID) -> bool:
    return (
        principal.is_org_admin
        or target_department_id in principal.managed_department_ids
    )


def responder_user_ids(
    session: Session, org_id: UUID, target_department_id: UUID
) -> set[UUID]:
    rows = session.exec(
        select(OrgMembership.user_id, RoleGrant.role, RoleGrant.department_id)
        .join(
            RoleGrant,
            (col(RoleGrant.org_id) == OrgMembership.org_id)
            & (col(RoleGrant.membership_id) == OrgMembership.id),
        )
        .join(User, col(User.id) == OrgMembership.user_id)
        .outerjoin(
            Employee,
            (col(Employee.org_id) == OrgMembership.org_id)
            & (col(Employee.id) == OrgMembership.employee_id),
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
    current: UUID | None = target_department_id
    seen = set()
    while current is not None and current not in seen:
        seen.add(current)
        managers = {
            user
            for user, role, dept in rows
            if role == Role.DEPT_MANAGER and dept == current
        }
        if managers:
            return managers
        current = department(session, org_id, current).parent_department_id
    return {user for user, role, _ in rows if role == Role.ORG_ADMIN}


def demand_public(session: Session, row: CapacityDemand) -> DemandPublic:
    events = session.exec(
        select(CapacityDemandEvent)
        .where(
            CapacityDemandEvent.org_id == row.org_id,
            CapacityDemandEvent.demand_id == row.id,
        )
        .order_by(col(CapacityDemandEvent.created_at), col(CapacityDemandEvent.id))
    ).all()
    result = DemandPublic.model_validate(row)
    result.events = [DemandEventPublic.model_validate(e) for e in events]
    return result


def get_demand(principal: Principal, session: Session, demand_id: UUID) -> DemandPublic:
    require_enabled(principal, session)
    row = session.exec(
        select(CapacityDemand).where(
            CapacityDemand.org_id == principal.org_id,
            CapacityDemand.id == demand_id,
        )
    ).one_or_none()
    if row is None or not (
        row.requester_user_id == principal.user_id
        or row.requesting_department_id in principal.managed_department_ids
        or represents_department(principal, row.target_department_id)
    ):
        raise CapacityNotFound("未找到该需求")
    return demand_public(session, row)


def list_demands(
    principal: Principal,
    session: Session,
    side: Literal["outgoing", "incoming"] = "outgoing",
) -> DemandList:
    require_enabled(principal, session)
    query = select(CapacityDemand).where(CapacityDemand.org_id == principal.org_id)
    if not principal.is_org_admin:
        query = query.where(
            or_(
                CapacityDemand.requester_user_id == principal.user_id,
                col(CapacityDemand.requesting_department_id).in_(
                    principal.managed_department_ids
                ),
            )
            if side == "outgoing"
            else col(CapacityDemand.target_department_id).in_(
                principal.managed_department_ids
            )
        )
    rows = session.exec(
        query.order_by(col(CapacityDemand.created_at).desc(), col(CapacityDemand.id))
    ).all()
    return DemandList(data=[demand_public(session, r) for r in rows], count=len(rows))


def submit_demand(
    principal: Principal,
    session: Session,
    target_department_id: UUID,
    capability_key: str,
    requesting_department_id: UUID,
    headcount: int,
    allocation_percent: int,
    window_start: date,
    window_end: date,
    description: str,
    minimum_level: str | None = None,
    *,
    via: Via = "web",
    client_name: str | None = None,
) -> DemandPublic:
    body = DemandInput(
        target_department_id=target_department_id,
        capability_key=capability_key,
        requesting_department_id=requesting_department_id,
        headcount=headcount,
        allocation_percent=allocation_percent,
        window_start=window_start,
        window_end=window_end,
        description=description,
        minimum_level=minimum_level,
    )
    require_requester(principal, session)
    if principal.user_id is None:
        raise CapacityForbidden()
    department(session, principal.org_id, requesting_department_id)
    if not principal.is_org_admin:
        if principal.managed_department_ids:
            allowed = requesting_department_id in principal.managed_department_ids
        else:
            allowed = (
                employee(principal, session, principal.employee_id).department_id
                == requesting_department_id
            )
        if not allowed:
            raise CapacityForbidden()
    reference = department_reference(
        session, principal.org_id, target_department_id, capability_key, minimum_level
    )
    row = CapacityDemand(
        org_id=principal.org_id,
        requester_user_id=principal.user_id,
        capability_name=reference.capability_name,
        **body.model_dump(),
    )
    session.add(row)
    session.flush()
    session.add(
        CapacityDemandEvent(
            org_id=principal.org_id,
            demand_id=row.id,
            kind=DemandEventKind.SUBMITTED,
            actor_user_id=principal.user_id,
            text=body.description,
            via=via,
        )
    )
    for user_id in sorted(
        responder_user_ids(session, principal.org_id, target_department_id)
        - {principal.user_id}
    ):
        add_item(
            session,
            InboxItem(
                org_id=principal.org_id,
                recipient_user_id=user_id,
                kind=InboxKind.CAPACITY,
                message=NOTICE,
            ),
        )
    audit(
        principal,
        session,
        "capacity.demand_submitted",
        "capacity_demand",
        row.id,
        via,
        client_name,
        {},
    )
    session.flush()
    return demand_public(session, row)
