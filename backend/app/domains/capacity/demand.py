from collections import defaultdict
from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from sqlmodel import Session, col, func, or_, select

from app.authz.policies import employee_scope_clause
from app.authz.principal import Principal
from app.domains.capability.search import (
    PeopleSearchInput,
    PeopleSearchRow,
    find_people,
)
from app.domains.capability.service import get_schema, list_capabilities
from app.domains.capacity.demand_schemas import (
    CapacityReference,
    CommitmentChange,
    DemandBasis,
    DemandBasisPerson,
    DemandEventPublic,
    DemandInput,
    DemandList,
    DemandPublic,
    ReplyInput,
    RespondInput,
)
from app.domains.capacity.models import (
    CapacityDemand,
    CapacityDemandEvent,
    DemandEventKind,
    DemandOutcome,
    DemandStatus,
    RequesterPolicy,
)
from app.domains.capacity.schemas import Via
from app.domains.capacity.settings import (
    CapacityConflict,
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
from app.domains.org.service import managed_scope
from app.models import User


class DemandReplyInvalid(HTTPException):
    def __init__(self) -> None:
        super().__init__(422, "答复会发给对方部门，请不要写本部门员工的姓名")


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


def demand_public(
    principal: Principal, session: Session, row: CapacityDemand
) -> DemandPublic:
    return demands_public(principal, session, [row])[0]


def demands_public(
    principal: Principal, session: Session, rows: list[CapacityDemand]
) -> list[DemandPublic]:
    if not rows:
        return []
    events = session.exec(
        select(CapacityDemandEvent)
        .where(
            CapacityDemandEvent.org_id == principal.org_id,
            col(CapacityDemandEvent.demand_id).in_([row.id for row in rows]),
        )
        .order_by(col(CapacityDemandEvent.created_at), col(CapacityDemandEvent.id))
    ).all()
    names = {
        user_id: employee_name or full_name or "企业管理员"
        for user_id, employee_name, full_name in session.exec(
            select(OrgMembership.user_id, Employee.display_name, User.full_name)
            .join(User, col(User.id) == OrgMembership.user_id)
            .outerjoin(
                Employee,
                (col(Employee.org_id) == OrgMembership.org_id)
                & (col(Employee.id) == OrgMembership.employee_id),
            )
            .where(
                OrgMembership.org_id == principal.org_id,
                col(OrgMembership.user_id).in_(
                    {
                        e.actor_user_id
                        for e in events
                        if e.kind == DemandEventKind.REPLIED
                    }
                ),
            )
        ).all()
    }
    grouped: dict[UUID, list[CapacityDemandEvent]] = defaultdict(list)
    for event in events:
        grouped[event.demand_id].append(event)
    results = []
    for row in rows:
        result = DemandPublic.model_validate(row)
        result.events = [
            DemandEventPublic(
                kind=e.kind,
                text=e.text,
                created_at=e.created_at,
                via=e.via,
                outcome=e.outcome,
                start_date=e.start_date,
                end_date=e.end_date,
                basis=CapacityReference.model_validate(e.basis)
                if e.kind == DemandEventKind.REPLIED and e.basis
                else None,
                **(
                    CommitmentChange.model_validate(e.basis).model_dump()
                    if e.kind == DemandEventKind.COMMITMENT_CHANGED
                    else {}
                ),
                responder_name=names.get(e.actor_user_id)
                if e.kind == DemandEventKind.REPLIED
                and represents_department(principal, row.target_department_id)
                else None,
            )
            for e in grouped[row.id]
        ]
        results.append(result)
    return results


def demand_row(
    principal: Principal, session: Session, demand_id: UUID, *, lock: bool = False
) -> CapacityDemand:
    require_enabled(principal, session)
    query = select(CapacityDemand).where(
        CapacityDemand.org_id == principal.org_id,
        CapacityDemand.id == demand_id,
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = session.exec(query).one_or_none()
    if row is None or not (
        row.requester_user_id == principal.user_id
        or row.requesting_department_id in principal.managed_department_ids
        or represents_department(principal, row.target_department_id)
    ):
        raise CapacityNotFound("未找到该需求")
    return row


def get_demand(principal: Principal, session: Session, demand_id: UUID) -> DemandPublic:
    row = demand_row(principal, session, demand_id)
    result = demand_public(principal, session, row)
    if represents_department(principal, row.target_department_id):
        result.basis_url = f"/orgs/{principal.org_id}/capacity/demands/{row.id}/basis"
    return result


def list_demands(
    principal: Principal,
    session: Session,
    side: Literal["outgoing", "incoming"] = "outgoing",
    skip: int = 0,
    limit: int = 50,
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
    count = session.exec(select(func.count()).select_from(query.subquery())).one()
    rows = list(
        session.exec(
            query.order_by(
                col(CapacityDemand.created_at).desc(), col(CapacityDemand.id)
            )
            .offset(skip)
            .limit(limit)
        ).all()
    )
    return DemandList(data=demands_public(principal, session, rows), count=count)


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
    return demand_public(principal, session, row)


def get_demand_basis(
    principal: Principal, session: Session, demand_id: UUID
) -> DemandBasis:
    row = demand_row(principal, session, demand_id)
    if not represents_department(principal, row.target_department_id):
        raise CapacityNotFound("未找到该需求")
    people: dict[UUID, PeopleSearchRow] = {}
    skip = 0
    while True:
        page = find_people(
            session,
            principal,
            PeopleSearchInput(
                department_id=row.target_department_id,
                capability_key=row.capability_key,
                minimum_level=row.minimum_level,
                skip=skip,
                limit=100,
            ),
        )
        for person in page.data:
            if person.level is not None:
                people.setdefault(person.employee_id, person)
        skip += len(page.data)
        if skip >= page.count or not page.data:
            break
    employees = list(
        session.exec(
            select(Employee).where(
                employee_scope_clause(principal),
                col(Employee.id).in_(people),
            )
        ).all()
    )
    workloads = {w.employee_id: w for w in views(principal, session, employees)}
    return DemandBasis(
        data=[
            DemandBasisPerson(**p.model_dump(), workload=workloads[p.employee_id])
            for p in sorted(
                people.values(), key=lambda p: (p.display_name, str(p.employee_id))
            )
        ],
        count=len(people),
        reference=department_reference(
            session,
            principal.org_id,
            row.target_department_id,
            row.capability_key,
            row.minimum_level,
        ),
    )


def validate_department_text(
    principal: Principal, session: Session, target_department_id: UUID, text: str
) -> None:
    target_ids = managed_scope(
        session, principal.org_id, frozenset({target_department_id})
    )
    people = session.exec(
        select(Employee).where(
            employee_scope_clause(principal),
            col(Employee.department_id).in_(target_ids),
            Employee.employment_status == EmploymentStatus.ACTIVE,
        )
    ).all()
    if any(
        private and private in text
        for person in people
        for private in (person.display_name, person.employee_code, str(person.id))
    ):
        raise DemandReplyInvalid()


def reply_demand(
    principal: Principal,
    session: Session,
    demand_id: UUID,
    outcome: DemandOutcome,
    text: str,
    revision: int,
    start_date: date | None = None,
    end_date: date | None = None,
    *,
    via: Via = "web",
    client_name: str | None = None,
) -> DemandPublic:
    body = ReplyInput(
        outcome=outcome,
        text=text,
        revision=revision,
        start_date=start_date,
        end_date=end_date,
    )
    row = demand_row(principal, session, demand_id, lock=True)
    if not represents_department(principal, row.target_department_id):
        raise CapacityNotFound("未找到该需求")
    if principal.user_id is None or principal.user_id == row.requester_user_id:
        raise CapacityForbidden()
    if row.revision != revision or row.status != DemandStatus.SUBMITTED:
        raise CapacityConflict()
    validate_department_text(principal, session, row.target_department_id, body.text)
    reference = department_reference(
        session,
        principal.org_id,
        row.target_department_id,
        row.capability_key,
        row.minimum_level,
    )
    session.add(
        CapacityDemandEvent(
            org_id=principal.org_id,
            demand_id=row.id,
            kind=DemandEventKind.REPLIED,
            actor_user_id=principal.user_id,
            text=body.text,
            outcome=body.outcome,
            start_date=body.start_date,
            end_date=body.end_date,
            basis=reference.model_dump(mode="json"),
            via=via,
        )
    )
    row.status = DemandStatus.REPLIED
    row.committed_start_date, row.committed_end_date = body.start_date, body.end_date
    row.revision += 1
    session.add(row)
    add_item(
        session,
        InboxItem(
            org_id=principal.org_id,
            recipient_user_id=row.requester_user_id,
            kind=InboxKind.CAPACITY,
            message=NOTICE,
        ),
    )
    audit(
        principal,
        session,
        "capacity.demand_replied",
        "capacity_demand",
        row.id,
        via,
        client_name,
        {},
    )
    session.flush()
    return demand_public(principal, session, row)


def respond_demand(
    principal: Principal,
    session: Session,
    demand_id: UUID,
    action: Literal["accept", "follow_up", "withdraw"],
    revision: int,
    text: str | None = None,
    *,
    via: Via = "web",
    client_name: str | None = None,
) -> DemandPublic:
    body = RespondInput(action=action, revision=revision, text=text)
    row = demand_row(principal, session, demand_id, lock=True)
    if principal.user_id is None or not (
        row.requester_user_id == principal.user_id
        or row.requesting_department_id in principal.managed_department_ids
    ):
        raise CapacityNotFound("未找到该需求")
    if row.revision != revision or row.status in (
        DemandStatus.ACCEPTED,
        DemandStatus.WITHDRAWN,
    ):
        raise CapacityConflict()
    if action != "withdraw" and row.status != DemandStatus.REPLIED:
        raise CapacityConflict()
    if action == "accept":
        latest_reply = session.exec(
            select(CapacityDemandEvent)
            .where(
                CapacityDemandEvent.org_id == principal.org_id,
                CapacityDemandEvent.demand_id == row.id,
                CapacityDemandEvent.kind == DemandEventKind.REPLIED,
            )
            .order_by(
                col(CapacityDemandEvent.created_at).desc(),
                col(CapacityDemandEvent.id).desc(),
            )
        ).first()
        if latest_reply is None or latest_reply.outcome == DemandOutcome.NOT_NOW:
            raise CapacityConflict()
    kind = {
        "accept": DemandEventKind.ACCEPTED,
        "follow_up": DemandEventKind.FOLLOW_UP,
        "withdraw": DemandEventKind.WITHDRAWN,
    }[action]
    row.status = {
        "accept": DemandStatus.ACCEPTED,
        "follow_up": DemandStatus.SUBMITTED,
        "withdraw": DemandStatus.WITHDRAWN,
    }[action]
    if action != "accept":
        row.committed_start_date = row.committed_end_date = None
    row.revision += 1
    session.add(row)
    session.add(
        CapacityDemandEvent(
            org_id=principal.org_id,
            demand_id=row.id,
            kind=kind,
            actor_user_id=principal.user_id,
            text=body.text or "",
            via=via,
        )
    )
    for user_id in sorted(
        responder_user_ids(session, principal.org_id, row.target_department_id)
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
        {
            "accept": "capacity.demand_accepted",
            "follow_up": "capacity.demand_follow_up",
            "withdraw": "capacity.demand_withdrawn",
        }[action],
        "capacity_demand",
        row.id,
        via,
        client_name,
        {},
    )
    session.flush()
    return demand_public(principal, session, row)
