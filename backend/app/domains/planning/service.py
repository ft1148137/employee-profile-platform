from datetime import date, timedelta
from typing import Any, cast
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
from app.domains.planning.schemas import (
    CasePerson,
    CasePublic,
    OptionInput,
    OptionPublic,
    ReviseInput,
)

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


class PlanningInvalidRevision(HTTPException):
    def __init__(self, message: str) -> None:
        super().__init__(400, message)


class PlanningLocked(HTTPException):
    def __init__(self) -> None:
        super().__init__(409, "该人员已锁定，请先解锁")


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
    candidates = cast("list[dict[str, str]]", latest.data.get("candidates", []))
    candidate_ids = {UUID(person["employee_id"]) for person in candidates}
    people = (
        session.exec(
            select(Employee.id, Employee.display_name).where(
                employee_scope_clause(principal),
                col(Employee.id).in_(candidate_ids),
            )
        ).all()
        if candidate_ids
        else []
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
        people=[
            CasePerson(employee_id=employee_id, display_name=name)
            for employee_id, name in people
        ],
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


def _date_label(value: str) -> str:
    day = date.fromisoformat(value)
    return f"{day.month} 月 {day.day} 日"


def _finding_key(finding: dict[str, Any]) -> tuple[Any, Any, Any]:
    return finding.get("code"), finding.get("employee_id"), finding.get("week_start")


def revise_option(
    principal: Principal,
    session: Session,
    access: CaseAccess,
    option_id: UUID,
    body: ReviseInput,
) -> OptionPublic:
    row, latest, demand = access
    base = session.exec(
        select(PlanningOption)
        .join(
            PlanningSnapshot,
            col(PlanningOption.snapshot_id) == col(PlanningSnapshot.id),
        )
        .where(
            PlanningOption.org_id == principal.org_id,
            PlanningOption.id == option_id,
            PlanningSnapshot.org_id == principal.org_id,
            PlanningSnapshot.case_id == row.id,
        )
    ).one_or_none()
    if base is None:
        raise PlanningNotFound()
    if (
        base.snapshot_id != latest.id
        or snapshot.current_fingerprint(session, principal, demand)
        != latest.fingerprint
    ):
        raise PlanningStale()
    if base.kind == OptionKind.BORROW or not base.params.get("assignees"):
        raise PlanningInvalidRevision("借调方案不能快捷修改，请在其他方案上修改")

    assignees = [
        dict(item) for item in cast("list[dict[str, Any]]", base.params["assignees"])
    ]
    selected = {item["employee_id"] for item in assignees}
    locks = set(cast("list[str]", base.params.get("locked_employee_ids", [])))
    people = cast("list[dict[str, Any]]", latest.data["candidates"])
    candidate_ids = {person["employee_id"] for person in people}
    names = dict(
        session.exec(
            select(Employee.id, Employee.display_name).where(
                employee_scope_clause(principal), col(Employee.id).in_(candidate_ids)
            )
        ).all()
    )

    def name(employee_id: str) -> str:
        return names.get(UUID(employee_id), "待确认人员")

    change = body.change
    if change == "replace":
        old, new = str(body.from_employee_id), str(body.to_employee_id)
        if old not in selected or new not in candidate_ids or new in selected:
            raise PlanningInvalidRevision("请选择方案中的人员和快照候选人")
        if old in locks:
            raise PlanningLocked()
        for item in assignees:
            if item["employee_id"] == old:
                item["employee_id"] = new
        description = f"把{name(old)}换成{name(new)}"
    elif change in {"dates", "allocation"}:
        target = str(body.employee_id) if body.employee_id is not None else None
        if target is not None and target not in selected:
            raise PlanningInvalidRevision("该人员不在方案中")
        changed = [
            item
            for item in assignees
            if target is None or item["employee_id"] == target
        ]
        if any(item["employee_id"] in locks for item in changed):
            if target is not None:
                raise PlanningLocked()
            changed = [item for item in changed if item["employee_id"] not in locks]
        if not changed:
            raise PlanningLocked()
        if change == "dates":
            assert body.start_date is not None and body.end_date is not None
            start, end = body.start_date.isoformat(), body.end_date.isoformat()
            if start > end:
                raise PlanningInvalidRevision("开始日期不能晚于结束日期")
            before = {(item["start_date"], item["end_date"]) for item in changed}
            percents: dict[str, int] = {}
            for item in changed:
                employee_id = item["employee_id"]
                percent = item["allocation_percent"]
                if employee_id in percents and percents[employee_id] != percent:
                    raise PlanningInvalidRevision(
                        "分阶段方案中同一人多段投入不同，请按原方案重新修改日期"
                    )
                percents[employee_id] = percent
            for item in changed:
                item["start_date"], item["end_date"] = start, end
            if len(changed) != len(percents):
                seen: set[str] = set()
                unique = []
                for item in assignees:
                    employee_id = item["employee_id"]
                    if employee_id in percents and employee_id in seen:
                        continue
                    unique.append(item)
                    seen.add(employee_id)
                assignees = unique
            if len(before) == 1:
                old_start, old_end = next(iter(before))
                parts = []
                if old_start != start:
                    parts.append(
                        f"开始日期从{_date_label(old_start)}改到{_date_label(start)}"
                    )
                if old_end != end:
                    parts.append(
                        f"结束日期从{_date_label(old_end)}改到{_date_label(end)}"
                    )
                description = (f"{name(target)}的" if target else "") + (
                    "，".join(parts) or "日期保持不变"
                )
            else:
                prefix = f"把{name(target)}的" if target else "把未锁定人员的"
                description = (
                    f"{prefix}日期改为{_date_label(start)}至{_date_label(end)}"
                )
        else:
            before_percents = {item["allocation_percent"] for item in changed}
            for item in changed:
                item["allocation_percent"] = body.allocation_percent
            prefix = f"把{name(target)}的" if target else "把未锁定人员的"
            description = (
                f"{prefix}投入从{next(iter(before_percents))}%改到{body.allocation_percent}%"
                if len(before_percents) == 1
                else f"{prefix}投入改为{body.allocation_percent}%"
            )
    elif change == "phased":
        if locks:
            raise PlanningLocked()
        split = body.split_date  # validated by ReviseInput
        assert split is not None
        if split.weekday() != 0 or any(
            not (
                date.fromisoformat(item["start_date"])
                < split
                <= date.fromisoformat(item["end_date"])
            )
            for item in assignees
        ):
            raise PlanningInvalidRevision("分阶段日期须为安排期间的周一")
        assignees = [
            part
            for item in assignees
            for part in (
                {**item, "end_date": (split - timedelta(days=1)).isoformat()},
                {**item, "start_date": split.isoformat()},
            )
        ]
        description = f"从{_date_label(split.isoformat())}起分阶段交付"
    else:
        ids = {str(employee_id) for employee_id in body.employee_ids or []}
        if not ids <= selected:
            raise PlanningInvalidRevision("锁定人员必须已在方案中")
        if change == "lock":
            locks |= ids
            description = "锁定" + "、".join(name(i) for i in sorted(ids))
        else:
            locks -= ids
            description = "解锁" + "、".join(name(i) for i in sorted(ids))

    params: dict[str, Any] = {
        "assignees": assignees,
        "locked_employee_ids": sorted(locks),
    }
    result = validator.validate(latest.data, params)
    old_findings = cast("list[dict[str, Any]]", base.findings)
    old_keys = {_finding_key(item) for item in old_findings}
    new_keys = {_finding_key(item) for item in result.findings}
    diff: dict[str, Any] = {
        field: {
            "before": base.verdict.value
            if field == "verdict"
            else base.summary.get(field),
            "after": result.verdict
            if field == "verdict"
            else result.summary.get(field),
        }
        for field in ("verdict", "finish_date", "covered_headcount", "gap_person_weeks")
    }
    diff["added_findings"] = [
        item for item in result.findings if _finding_key(item) not in old_keys
    ]
    diff["resolved_findings"] = [
        item for item in old_findings if _finding_key(item) not in new_keys
    ]
    option = PlanningOption(
        org_id=principal.org_id,
        snapshot_id=latest.id,
        kind=OptionKind.CUSTOM,
        params=params,
        verdict=PlanVerdict(result.verdict),
        findings=result.findings,
        summary={
            **result.summary,
            "base_option_id": str(base.id),
            "changes": description,
            "diff": diff,
        },
    )
    session.add(option)
    session.flush()
    return OptionPublic.model_validate(option, from_attributes=True)
