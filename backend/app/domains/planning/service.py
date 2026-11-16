from typing import cast
from uuid import UUID, uuid7

from fastapi import HTTPException
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import Session, col, select

from app.authz.policies import employee_scope_clause
from app.authz.principal import Principal
from app.domains.base import utc_now
from app.domains.capacity.demand import (
    demand_row,
    represents_department,
)
from app.domains.capacity.models import CapacityDemand
from app.domains.capacity.settings import require_enabled
from app.domains.org.models import Employee
from app.domains.planning import simulator, snapshot, validator
from app.domains.planning.models import (
    OptionKind,
    PlanningCase,
    PlanningOption,
    PlanningSnapshot,
    PlanVerdict,
)
from app.domains.planning.schemas import CasePublic, OptionInput, OptionPublic

CaseAccess = tuple[PlanningCase, PlanningSnapshot, CapacityDemand]


class PlanningStale(HTTPException):
    def __init__(self) -> None:
        super().__init__(409, "数据已变化，请重新模拟")


class PlanningNotFound(HTTPException):
    def __init__(self) -> None:
        super().__init__(404, "未找到该方案")


class PlanningCandidateOutOfScope(HTTPException):
    def __init__(self) -> None:
        super().__init__(409, "部分候选人已不在当前管辖范围内，请重新模拟")


def _authorized_demand(
    principal: Principal, session: Session, demand_id: UUID
) -> CapacityDemand:
    row = demand_row(principal, session, demand_id)
    if not represents_department(principal, row.target_department_id):
        raise PlanningNotFound()
    return row


def authorized_case(
    principal: Principal, session: Session, case_id: UUID
) -> CaseAccess:
    require_enabled(principal, session)
    row = session.exec(
        select(PlanningCase).where(
            PlanningCase.org_id == principal.org_id, PlanningCase.id == case_id
        )
    ).one_or_none()
    if row is None:
        raise PlanningNotFound()
    demand = _authorized_demand(principal, session, row.demand_id)
    latest = session.exec(
        select(PlanningSnapshot)
        .where(
            PlanningSnapshot.org_id == principal.org_id,
            PlanningSnapshot.case_id == row.id,
        )
        .order_by(
            col(PlanningSnapshot.created_at).desc(), col(PlanningSnapshot.id).desc()
        )
    ).first()
    if latest is None:
        raise PlanningNotFound()
    return row, latest, demand


def _view(
    principal: Principal,
    session: Session,
    row: PlanningCase,
    latest: PlanningSnapshot,
    demand: CapacityDemand,
) -> CasePublic:
    options = session.exec(
        select(PlanningOption)
        .where(
            PlanningOption.org_id == principal.org_id,
            PlanningOption.snapshot_id == latest.id,
        )
        .order_by(col(PlanningOption.created_at), col(PlanningOption.id))
    ).all()
    _, notes = simulator.simulate(latest.data)
    order = {
        kind: i
        for i, kind in enumerate(
            (
                OptionKind.DIRECT,
                OptionKind.NEWCOMER,
                OptionKind.DELAY,
                OptionKind.REDUCE_SCOPE,
                OptionKind.PHASED,
                OptionKind.BORROW,
                OptionKind.CUSTOM,
            )
        )
    }
    options = sorted(
        options, key=lambda option: (order[option.kind], option.created_at, option.id)
    )
    return CasePublic(
        id=row.id,
        demand_id=row.demand_id,
        snapshot_id=latest.id,
        snapshot_created_at=latest.created_at,
        data=latest.data,
        stale=snapshot.current_fingerprint(session, principal, demand)
        != latest.fingerprint,
        notes=notes,
        options=[OptionPublic.model_validate(o, from_attributes=True) for o in options],
    )


def create_case(principal: Principal, session: Session, demand_id: UUID) -> CasePublic:
    demand = _authorized_demand(principal, session, demand_id)
    assert principal.user_id is not None
    session.exec(
        insert(PlanningCase)
        .values(
            id=uuid7(),
            org_id=principal.org_id,
            demand_id=demand_id,
            created_by_user_id=principal.user_id,
            created_at=utc_now(),
        )
        .on_conflict_do_nothing(index_elements=["org_id", "demand_id"])
    )
    row = session.exec(
        select(PlanningCase).where(
            PlanningCase.org_id == principal.org_id,
            PlanningCase.demand_id == demand_id,
        )
    ).one()
    data, fingerprint, departments = snapshot.build(session, principal, demand)
    latest = PlanningSnapshot(
        org_id=principal.org_id,
        case_id=row.id,
        data=data,
        fingerprint=fingerprint,
        created_by_user_id=principal.user_id,
    )
    session.add(latest)
    session.flush()
    options, _ = simulator.simulate(data)
    if departments:
        options.append(simulator.borrow(data, departments))
    for generated in options:
        session.add(
            PlanningOption(
                org_id=principal.org_id,
                snapshot_id=latest.id,
                kind=OptionKind(generated["kind"]),
                params=generated["params"],
                verdict=PlanVerdict(generated["verdict"]),
                findings=generated["findings"],
                summary=generated["summary"],
            )
        )
    session.flush()
    return _view(principal, session, row, latest, demand)


def get_case(principal: Principal, session: Session, case_id: UUID) -> CasePublic:
    row, latest, demand = authorized_case(principal, session, case_id)
    candidates = cast("list[dict[str, str]]", latest.data.get("candidates", []))
    candidate_ids = {UUID(person["employee_id"]) for person in candidates}
    if candidate_ids:
        visible_ids = set(
            session.exec(
                select(Employee.id).where(
                    employee_scope_clause(principal),
                    col(Employee.id).in_(candidate_ids),
                )
            ).all()
        )
        if visible_ids != candidate_ids:
            raise PlanningCandidateOutOfScope()
    return _view(principal, session, row, latest, demand)


def create_option(
    principal: Principal,
    session: Session,
    access: CaseAccess,
    body: OptionInput,
) -> OptionPublic:
    _, latest, demand = access
    if snapshot.current_fingerprint(session, principal, demand) != latest.fingerprint:
        raise PlanningStale()
    params = body.model_dump(mode="json")
    result = validator.validate(latest.data, params)
    option = PlanningOption(
        org_id=principal.org_id,
        snapshot_id=latest.id,
        kind=OptionKind.CUSTOM,
        params=params,
        verdict=PlanVerdict(result.verdict),
        findings=result.findings,
        summary=result.summary,
    )
    session.add(option)
    session.flush()
    return OptionPublic.model_validate(option, from_attributes=True)
