import re
from collections import defaultdict
from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import JsonValue
from sqlmodel import Session, col, func, or_, select

from app.authz.policies import employee_scope_clause
from app.authz.principal import Principal
from app.domains.capability.models import CapabilityDefinition
from app.domains.capability.search import (
    PeopleSearchInput,
    PeopleSearchRow,
    find_people,
)
from app.domains.capability.service import get_schema, list_capabilities
from app.domains.capacity.demand_schemas import (
    CapacityReference,
    CommitmentChange,
    CountCapacityReference,
    DemandBasis,
    DemandBasisPerson,
    DemandEventPublic,
    DemandExtraCapability,
    DemandHistory,
    DemandHistoryEvent,
    DemandInput,
    DemandList,
    DemandOtherPerson,
    DemandProject,
    DemandPublic,
    DetailedCapacityReference,
    LimitedCapacityReference,
    ProjectRoleInput,
    ProjectRoleSummary,
    ReplyInput,
    RespondInput,
)
from app.domains.capacity.models import (
    CapacityDemand,
    CapacityDemandDependency,
    CapacityDemandEvent,
    CapacityProject,
    DemandEventKind,
    DemandOutcome,
    DemandStatus,
    ProposalStatus,
    RequesterPolicy,
)
from app.domains.capacity.schemas import REASON_LABELS, ReasonCode, Via
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
from app.domains.org.service import department_ancestry, managed_scope
from app.models import User

LOAD_WORDS = (
    "超负荷",
    "基本满",
    "有余量",
    "饱和",
    "已满",
    "满负荷",
    "满员",
    "排满",
    "人手紧",
    "人手不足",
    "忙不过来",
    "空闲",
    "有空",
    "闲置",
    "余量",
)
DEMAND_NOT_FOUND = "未找到该需求，或你无权查看（仅发起人及双方部门经理可见）。"


class DemandReplyInvalid(HTTPException):
    def __init__(self) -> None:
        super().__init__(422, "回复将发送给对方部门，请勿填写本部门员工姓名")


class DemandReplyLoadInvalid(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            422, "回复将发送给对方部门，请勿填写负荷描述，仅说明能否承接、时间及条件"
        )


class RejectionReasonInvalid(HTTPException):
    def __init__(self, message: str) -> None:
        super().__init__(422, message)


def require_rejection_reason(reason_code: ReasonCode | None, text: str | None) -> None:
    if reason_code is None:
        raise RejectionReasonInvalid(
            f"请选择原因类型（{' / '.join(REASON_LABELS.values())}）"
        )
    if reason_code == "other" and not (text and text.strip()):
        raise RejectionReasonInvalid("选择「其他」时必须填写说明")


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
) -> DetailedCapacityReference:
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
    result = DetailedCapacityReference(
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
    return reference_for_viewer(
        principal,
        target_department_id,
        department_reference(
            session,
            principal.org_id,
            target_department_id,
            capability_key,
            minimum_level,
        ),
    )


def reference_for_viewer(
    principal: Principal,
    target_department_id: UUID,
    reference: DetailedCapacityReference,
    *,
    requester_view: bool = False,
) -> CapacityReference:
    if not requester_view and represents_department(principal, target_department_id):
        return reference
    if 0 < reference.count < 3:
        return LimitedCapacityReference(
            department_name=reference.department_name,
            capability_name=reference.capability_name,
            limited=True,
        )
    return CountCapacityReference(
        department_name=reference.department_name,
        capability_name=reference.capability_name,
        count=reference.count,
    )


def represents_department(principal: Principal, target_department_id: UUID) -> bool:
    return (
        principal.is_org_admin
        or target_department_id in principal.managed_department_ids
    )


def responder_user_ids(
    session: Session,
    org_id: UUID,
    target_department_id: UUID,
    *,
    fallback_to_admin: bool = True,
) -> set[UUID]:
    return responders_by_department(
        session, org_id, {target_department_id}, fallback_to_admin=fallback_to_admin
    )[target_department_id]


def responders_by_department(
    session: Session,
    org_id: UUID,
    target_department_ids: set[UUID],
    *,
    fallback_to_admin: bool = True,
) -> dict[UUID, set[UUID]]:
    if not target_department_ids:
        return {}
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
    admins: set[UUID] = set()
    managers: dict[UUID, set[UUID]] = defaultdict(set)
    for user_id, role, department_id in rows:
        if role == Role.DEPT_MANAGER and department_id is not None:
            managers[department_id].add(user_id)
        elif role == Role.ORG_ADMIN and fallback_to_admin:
            admins.add(user_id)
    departments = {
        row.id: row
        for row in session.exec(
            select(Department).where(Department.org_id == org_id)
        ).all()
    }
    if target_department_ids - departments.keys():
        raise CapacityNotFound("未找到该部门")
    return {
        target: next(
            (
                managers[ancestor]
                for ancestor in department_ancestry(departments, target)
                if ancestor in managers
            ),
            admins,
        ).copy()
        for target in target_department_ids
    }


def _public_extra_capability(
    item: dict[str, JsonValue], definitions: dict[str, CapabilityDefinition]
) -> DemandExtraCapability:
    key = str(item["key"])
    minimum = item.get("minimum_level")
    definition = definitions.get(key)
    rubric = (definition.level_rubric or {}) if definition else {}
    levels = rubric.get("levels", [])
    label = None
    if minimum is not None and isinstance(levels, list):
        label = next(
            (
                level_label
                for level in levels
                if isinstance(level, dict)
                and level.get("key") == minimum
                and isinstance(level_label := level.get("label"), str)
            ),
            None,
        )
    return DemandExtraCapability.model_validate(
        {
            **item,
            "capability_name": definition.name if definition else key,
            "minimum_level_label": label,
        }
    )


def demand_public(
    principal: Principal, session: Session, row: CapacityDemand
) -> DemandPublic:
    return demands_public(principal, session, [row])[0]


def demands_public(
    principal: Principal,
    session: Session,
    rows: list[CapacityDemand],
    *,
    requester_view: bool = False,
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
    responder_ids = {
        event.actor_user_id for event in events if event.kind == DemandEventKind.REPLIED
    }
    names = (
        {
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
                    col(OrgMembership.user_id).in_(responder_ids),
                )
            ).all()
        }
        if responder_ids and not requester_view
        else {}
    )
    project_ids = {row.project_id for row in rows if row.project_id is not None}
    projects = (
        dict(
            session.exec(
                select(CapacityProject.id, CapacityProject.title).where(
                    CapacityProject.org_id == principal.org_id,
                    col(CapacityProject.id).in_(project_ids),
                )
            ).all()
        )
        if project_ids and not requester_view
        else {}
    )
    definitions = (
        {
            definition.key: definition
            for definition in list_capabilities(
                session, principal.org_id, get_schema(session, principal.org_id).id
            )
        }
        if any(row.extra_capabilities for row in rows)
        else {}
    )
    grouped: dict[UUID, list[CapacityDemandEvent]] = defaultdict(list)
    for event in events:
        grouped[event.demand_id].append(event)
    results = []
    for row in rows:
        result = DemandPublic.model_validate(
            row.model_dump(include=set(DemandPublic.model_fields))
            | {
                "extra_capabilities": [
                    _public_extra_capability(item, definitions)
                    for item in row.extra_capabilities
                ]
            }
        )
        if (
            row.project_id is not None
            and row.project_id in projects
            and row.role_name is not None
        ):
            result.project = DemandProject(
                id=row.project_id,
                title=projects[row.project_id],
                role_name=row.role_name,
            )
        target_view = not requester_view and represents_department(
            principal, row.target_department_id
        )
        result.can_reply = (
            target_view
            and row.status == DemandStatus.SUBMITTED
            and principal.user_id is not None
            and principal.user_id != row.requester_user_id
        )
        result.events = [
            DemandEventPublic(
                kind=e.kind,
                text=e.text,
                created_at=e.created_at,
                via=e.via,
                outcome=e.outcome,
                start_date=e.start_date,
                end_date=e.end_date,
                basis=reference_for_viewer(
                    principal,
                    row.target_department_id,
                    DetailedCapacityReference.model_validate(e.basis),
                    requester_view=requester_view,
                )
                if e.kind == DemandEventKind.REPLIED and e.basis
                else None,
                **(
                    CommitmentChange.model_validate(e.basis).model_dump()
                    if e.kind == DemandEventKind.COMMITMENT_CHANGED
                    else {}
                ),
                responder_name=names.get(e.actor_user_id)
                if e.kind == DemandEventKind.REPLIED and target_view
                else None,
            )
            for e in grouped[row.id]
        ]
        results.append(result)
    return results


def project_role_summary(
    row: CapacityDemand, department_name: str
) -> ProjectRoleSummary:
    assert row.role_name is not None
    return ProjectRoleSummary(
        id=row.id,
        role_name=row.role_name,
        target_department_name=department_name,
        status=row.status,
        window_start=row.window_start,
        window_end=row.window_end,
        committed_start_date=row.committed_start_date,
        committed_end_date=row.committed_end_date,
    )


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
        raise CapacityNotFound(DEMAND_NOT_FOUND)
    return row


def get_demand(
    principal: Principal,
    session: Session,
    demand_id: UUID,
    *,
    include_previous: bool = False,
) -> DemandPublic:
    from app.domains.capacity.proposal import public

    row = demand_row(principal, session, demand_id)
    result = demand_public(principal, session, row)
    if result.project is not None:
        result.project.prerequisites = [
            project_role_summary(role, name)
            for role, name in session.exec(
                select(CapacityDemand, Department.name)
                .join(
                    CapacityDemandDependency,
                    (col(CapacityDemandDependency.org_id) == CapacityDemand.org_id)
                    & (
                        col(CapacityDemandDependency.depends_on_demand_id)
                        == CapacityDemand.id
                    ),
                )
                .join(
                    Department,
                    (col(Department.org_id) == CapacityDemand.org_id)
                    & (col(Department.id) == CapacityDemand.target_department_id),
                )
                .where(
                    CapacityDemand.org_id == principal.org_id,
                    CapacityDemand.project_id == result.project.id,
                    CapacityDemandDependency.project_id == result.project.id,
                    CapacityDemandDependency.demand_id == row.id,
                )
                .order_by(col(CapacityDemand.created_at), col(CapacityDemand.id))
            ).all()
        ]
    result.proposal = public(principal, session, row, include_previous=include_previous)
    if represents_department(principal, row.target_department_id):
        result.basis_url = f"/orgs/{principal.org_id}/capacity/demands/{row.id}/basis"
    return result


def decision_history(
    principal: Principal, session: Session, row: CapacityDemand
) -> DemandHistory:
    from app.domains.capacity import proposal

    detailed = represents_department(principal, row.target_department_id)
    events = session.exec(
        select(CapacityDemandEvent)
        .where(
            CapacityDemandEvent.org_id == principal.org_id,
            CapacityDemandEvent.demand_id == row.id,
        )
        .order_by(col(CapacityDemandEvent.created_at), col(CapacityDemandEvent.id))
    ).all()
    return DemandHistory(
        proposals=proposal.decision_history(principal, session, row),
        events=[
            DemandHistoryEvent(
                kind=event.kind,
                created_at=event.created_at,
                text=event.text
                if detailed or event.kind != DemandEventKind.REPLIED
                else None,
                outcome=event.outcome,
                reason_code=event.reason_code,
            )
            for event in events
        ],
    )


def list_demands(
    principal: Principal,
    session: Session,
    side: Literal["outgoing", "incoming"] = "outgoing",
    skip: int = 0,
    limit: int = 50,
    status: DemandStatus | None = None,
    actionable_only: bool = False,
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
    if status is not None:
        query = query.where(CapacityDemand.status == status)
    if actionable_only:
        if principal.user_id is None or (
            not principal.is_org_admin and not principal.managed_department_ids
        ):
            return DemandList(data=[], count=0)
        query = query.where(
            CapacityDemand.status == DemandStatus.SUBMITTED,
            CapacityDemand.requester_user_id != principal.user_id,
        )
        if not principal.is_org_admin and side == "outgoing":
            query = query.where(
                col(CapacityDemand.target_department_id).in_(
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
    row = create_demand(principal, session, body, via=via, client_name=client_name)
    return demand_public(principal, session, row)


def require_requesting_department(
    principal: Principal, session: Session, requesting_department_id: UUID
) -> UUID:
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
    return principal.user_id


def create_demand(
    principal: Principal,
    session: Session,
    body: DemandInput,
    *,
    project_id: UUID | None = None,
    role: ProjectRoleInput | None = None,
    via: Via = "web",
    client_name: str | None = None,
) -> CapacityDemand:
    requester = require_requesting_department(
        principal, session, body.requesting_department_id
    )
    target_department_id = body.target_department_id
    reference = department_reference(
        session,
        principal.org_id,
        target_department_id,
        body.capability_key,
        body.minimum_level,
    )
    row = CapacityDemand(
        org_id=principal.org_id,
        requester_user_id=requester,
        capability_name=reference.capability_name,
        project_id=project_id,
        role_name=role.role_name if role else None,
        extra_capabilities=[
            item.model_dump(mode="json") for item in role.extra_capabilities
        ]
        if role
        else [],
        **body.model_dump(),
    )
    session.add(row)
    session.flush()
    session.add(
        CapacityDemandEvent(
            org_id=principal.org_id,
            demand_id=row.id,
            kind=DemandEventKind.SUBMITTED,
            actor_user_id=requester,
            text=body.description,
            via=via,
        )
    )
    for user_id in sorted(
        responder_user_ids(session, principal.org_id, target_department_id)
        - {requester}
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
    return row


def qualified_people(
    principal: Principal, session: Session, row: CapacityDemand, department_id: UUID
) -> dict[UUID, PeopleSearchRow]:
    people: dict[UUID, PeopleSearchRow] = {}
    skip = 0
    while True:
        page = find_people(
            session,
            principal,
            PeopleSearchInput(
                department_id=department_id,
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
    return people


def get_demand_basis(
    principal: Principal, session: Session, demand_id: UUID
) -> DemandBasis:
    row = demand_row(principal, session, demand_id)
    if not represents_department(principal, row.target_department_id):
        raise CapacityNotFound(DEMAND_NOT_FOUND)
    people = qualified_people(principal, session, row, row.target_department_id)
    employees = list(
        session.exec(
            select(Employee).where(
                employee_scope_clause(principal),
                col(Employee.id).in_(people),
            )
        ).all()
    )
    workloads = {w.employee_id: w for w in views(principal, session, employees)}
    target_ids = managed_scope(
        session, principal.org_id, frozenset({row.target_department_id})
    )
    others = session.exec(
        select(Employee)
        .where(
            employee_scope_clause(principal),
            Employee.employment_status == EmploymentStatus.ACTIVE,
            col(Employee.department_id).in_(target_ids),
            col(Employee.id).not_in(people),
        )
        .order_by(Employee.display_name, col(Employee.id))
    ).all()
    return DemandBasis(
        data=[
            DemandBasisPerson(**p.model_dump(), workload=workloads[p.employee_id])
            for p in sorted(
                people.values(), key=lambda p: (p.display_name, str(p.employee_id))
            )
        ],
        others=[
            DemandOtherPerson(employee_id=p.id, display_name=p.display_name)
            for p in others
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


def validate_load_words(
    principal: Principal,
    session: Session,
    demand_row: CapacityDemand,
    text: str,
    reference: DetailedCapacityReference | None = None,
) -> None:
    checked_text = text.replace("已满足", "").replace("空调", "")
    if not (
        any(word in checked_text for word in LOAD_WORDS)
        or re.search(r"负荷[^。；，,;\n]{0,4}(尚未|未)确认", checked_text)
    ):
        return
    if reference is None:
        reference = department_reference(
            session,
            principal.org_id,
            demand_row.target_department_id,
            demand_row.capability_key,
            demand_row.minimum_level,
        )
    if reference.count < 3:
        raise DemandReplyLoadInvalid()


def validate_reply_text(
    principal: Principal, session: Session, demand_row: CapacityDemand, text: str
) -> DetailedCapacityReference:
    reference = department_reference(
        session,
        principal.org_id,
        demand_row.target_department_id,
        demand_row.capability_key,
        demand_row.minimum_level,
    )
    validate_load_words(principal, session, demand_row, text, reference)
    return reference


def reply_demand(
    principal: Principal,
    session: Session,
    demand_id: UUID,
    outcome: DemandOutcome,
    text: str,
    revision: int,
    start_date: date | None = None,
    end_date: date | None = None,
    option_id: UUID | None = None,
    outside_basis: bool = False,
    *,
    reason_code: ReasonCode | None = None,
    via: Via = "web",
    client_name: str | None = None,
) -> DemandPublic:
    if option_id is not None or outside_basis:
        # Option composition is a REST operation; assistants require confirmation.
        raise CapacityForbidden()
    body = ReplyInput(
        outcome=outcome,
        text=text,
        revision=revision,
        start_date=start_date,
        end_date=end_date,
        reason_code=reason_code,
    )
    row = demand_row(principal, session, demand_id, lock=True)
    if not represents_department(principal, row.target_department_id):
        raise CapacityNotFound(DEMAND_NOT_FOUND)
    if principal.user_id is None or principal.user_id == row.requester_user_id:
        raise CapacityForbidden()
    if row.revision != revision or row.status != DemandStatus.SUBMITTED:
        raise CapacityConflict()
    if body.outcome == DemandOutcome.NOT_NOW:
        require_rejection_reason(body.reason_code, body.text)
    validate_department_text(principal, session, row.target_department_id, body.text)
    reference = validate_reply_text(principal, session, row, body.text)
    session.add(
        CapacityDemandEvent(
            org_id=principal.org_id,
            demand_id=row.id,
            kind=DemandEventKind.REPLIED,
            actor_user_id=principal.user_id,
            text=body.text,
            outcome=body.outcome,
            reason_code=body.reason_code
            if body.outcome == DemandOutcome.NOT_NOW
            else None,
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
    reason_code: ReasonCode | None = None,
    via: Via = "web",
    client_name: str | None = None,
) -> DemandPublic:
    body = RespondInput(
        action=action, revision=revision, text=text, reason_code=reason_code
    )
    row = demand_row(principal, session, demand_id, lock=True)
    if principal.user_id is None or not (
        row.requester_user_id == principal.user_id
        or row.requesting_department_id in principal.managed_department_ids
    ):
        raise CapacityNotFound(DEMAND_NOT_FOUND)
    from app.domains.capacity import proposal

    latest = proposal.latest(session, principal.org_id, demand_id, lock=True)
    if (
        latest is not None
        and not (
            action == "withdraw"
            and row.status == DemandStatus.SUBMITTED
            and revision == row.revision
            and latest.kind == "initial"
            and latest.status == ProposalStatus.WITHDRAWN
        )
        and (
            revision <= latest.demand_revision
            or not proposal.reply_superseded(row, latest)
        )
    ):
        return proposal.respond(
            principal,
            session,
            row,
            latest,
            action,
            revision,
            text,
            reason_code=body.reason_code,
            via=via,
        )
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
