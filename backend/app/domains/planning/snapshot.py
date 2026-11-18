import hashlib
import json
from datetime import date, timedelta
from typing import Any
from uuid import UUID

from pydantic import JsonValue
from sqlmodel import Session, col, select

from app.authz.policies import employee_scope_clause
from app.authz.principal import Principal
from app.domains.base import utc_now
from app.domains.capability.models import CapabilityDefinition
from app.domains.capability.service import get_schema
from app.domains.capacity.calendar import FRESH_DAYS, loads, this_week, week_start
from app.domains.capacity.demand import department_reference, get_demand_basis
from app.domains.capacity.models import (
    AssignmentStatus,
    CapacityAssignment,
    CapacityDemand,
    CapacityProposal,
    CapacityProposalLine,
    CapacityUnavailability,
    CapacityWeek,
    DemandStatus,
    ProposalStatus,
)
from app.domains.org.models import Department, Employee, EmploymentStatus
from app.domains.org.service import managed_scope
from app.domains.profile.models import ProfileVersion


def _level_order(capability: CapabilityDefinition | None) -> list[str]:
    rubric = capability.level_rubric if capability else None
    raw_levels = (rubric or {}).get("levels", [])
    return (
        [str(v["key"]) for v in raw_levels if isinstance(v, dict) and "key" in v]
        if isinstance(raw_levels, list)
        else []
    )


def _extra_capabilities(
    session: Session, org_id: UUID, demand: CapacityDemand
) -> list[dict[str, JsonValue]]:
    if not demand.extra_capabilities:
        return []
    keys = [item["key"] for item in demand.extra_capabilities]
    schema = get_schema(session, org_id)
    definitions = {
        capability.key: capability
        for capability in session.exec(
            select(CapabilityDefinition).where(
                CapabilityDefinition.org_id == org_id,
                CapabilityDefinition.schema_version_id == schema.id,
                col(CapabilityDefinition.key).in_(keys),
            )
        )
    }
    extras: list[dict[str, JsonValue]] = []
    for index, item in enumerate(demand.extra_capabilities, 1):
        capability = definitions.get(str(item["key"]))
        levels: list[JsonValue] = [*_level_order(capability)]
        extras.append(
            {
                **item,
                "name": capability.name if capability else f"额外能力 {index}",
                "levels": levels,
            }
        )
    return extras


def external_departments(
    session: Session, org_id: UUID, demand: CapacityDemand
) -> list[dict[str, object]]:
    rows = session.exec(select(Department).where(Department.org_id == org_id)).all()
    by_id = {d.id: d for d in rows}
    target = demand.target_department_id
    ancestors = set()
    current = target
    while current in by_id:
        ancestors.add(current)
        parent = by_id[current].parent_department_id
        if parent is None:
            break
        current = parent
    descendants = {target}
    changed = True
    while changed:
        changed = False
        for row in rows:
            if row.parent_department_id in descendants and row.id not in descendants:
                descendants.add(row.id)
                changed = True
    excluded = ancestors | descendants
    top_level = sorted(
        (
            d
            for d in rows
            if d.id not in excluded
            and (d.parent_department_id in excluded or d.parent_department_id is None)
        ),
        key=lambda d: (d.name, d.id),
    )
    departments: list[dict[str, object]] = []
    for row in top_level:
        reference = department_reference(
            session, org_id, row.id, demand.capability_key, demand.minimum_level
        )
        if reference.count:
            departments.append(
                {
                    "department_id": str(row.id),
                    "department_name": reference.department_name,
                    "count": reference.count,
                }
            )
    return departments


def _inputs(
    session: Session,
    principal: Principal,
    demand: CapacityDemand,
) -> tuple[
    list[Employee],
    dict[UUID, dict[str, str | None]],
    dict[str, Any],
    list[dict[str, object]],
    list[CapacityAssignment],
]:
    scope = managed_scope(
        session, principal.org_id, frozenset({demand.target_department_id})
    )
    candidates = list(
        session.exec(
            select(Employee).where(
                employee_scope_clause(principal),
                Employee.employment_status == EmploymentStatus.ACTIVE,
                col(Employee.department_id).in_(scope),
            )
        ).all()
    )
    ids = {e.id for e in candidates}
    current_week = this_week(session, principal.org_id)
    start = max(week_start(demand.window_start), current_week)
    end = week_start(demand.window_end) + timedelta(weeks=9)
    versions = list(
        session.exec(
            select(ProfileVersion)
            .where(
                ProfileVersion.org_id == principal.org_id,
                col(ProfileVersion.subject_employee_id).in_(ids),
            )
            .order_by(
                col(ProfileVersion.subject_employee_id),
                col(ProfileVersion.revision).desc(),
            )
        ).all()
    )
    latest: dict[UUID, ProfileVersion] = {}
    for version in versions:
        latest.setdefault(version.subject_employee_id, version)
    keys = {
        str(id): key
        for id, key in session.exec(
            select(CapabilityDefinition.id, CapabilityDefinition.key).where(
                CapabilityDefinition.org_id == principal.org_id
            )
        ).all()
    }
    extras = _extra_capabilities(session, principal.org_id, demand)
    relevant_keys = {demand.capability_key, *(str(item["key"]) for item in extras)}
    claims: dict[UUID, dict[str, str | None]] = {}
    for eid, version in latest.items():
        raw = version.snapshot.get("claims", [])
        if isinstance(raw, list):
            for claim in raw:
                if not isinstance(claim, dict):
                    continue
                key = keys.get(str(claim.get("capability_definition_id")))
                if key is not None and key in relevant_keys:
                    level = claim.get("level")
                    claims.setdefault(eid, {}).setdefault(
                        key, level if isinstance(level, str) else None
                    )
    weeks = list(
        session.exec(
            select(CapacityWeek).where(
                CapacityWeek.org_id == principal.org_id,
                col(CapacityWeek.employee_id).in_(ids),
                CapacityWeek.week_start >= start,
                CapacityWeek.week_start < end,
            )
        ).all()
    )
    unavailable = list(
        session.exec(
            select(CapacityUnavailability).where(
                CapacityUnavailability.org_id == principal.org_id,
                col(CapacityUnavailability.employee_id).in_(ids),
                CapacityUnavailability.end_date >= start,
                CapacityUnavailability.start_date < end,
            )
        ).all()
    )
    assignments = list(
        session.exec(
            select(CapacityAssignment).where(
                CapacityAssignment.org_id == principal.org_id,
                col(CapacityAssignment.employee_id).in_(ids),
                CapacityAssignment.status == AssignmentStatus.ACTIVE,
                CapacityAssignment.due_date >= start,
            )
        ).all()
    )
    departments = external_departments(session, principal.org_id, demand)
    holds = session.exec(
        select(CapacityProposalLine, CapacityProposal)
        .join(
            CapacityProposal,
            (col(CapacityProposal.org_id) == CapacityProposalLine.org_id)
            & (col(CapacityProposal.id) == CapacityProposalLine.proposal_id),
        )
        .where(
            CapacityProposalLine.org_id == principal.org_id,
            col(CapacityProposalLine.employee_id).in_(ids),
            CapacityProposal.demand_id != demand.id,
            CapacityProposal.status == ProposalStatus.PENDING,
            CapacityProposal.hold_expires_at > utc_now(),
            CapacityProposalLine.end_date >= start,
            CapacityProposalLine.start_date < end,
        )
    ).all()
    cutoff = utc_now() - timedelta(days=FRESH_DAYS)
    commitment = (
        session.exec(
            select(CapacityProposal.id)
            .where(
                CapacityProposal.org_id == principal.org_id,
                CapacityProposal.demand_id == demand.id,
                CapacityProposal.status == ProposalStatus.COMMITTED,
            )
            .limit(1)
        ).first()
        if demand.status == DemandStatus.ACCEPTED
        else None
    )
    raw_inputs: dict[str, Any] = {
        "current_week": current_week.isoformat(),
        "revision": demand.revision,
        **({"change": True} if commitment is not None else {}),
        **({"extra_capabilities": extras} if extras else {}),
        "candidates": [
            (
                str(e.id),
                str(e.department_id),
                str(e.employment_status),
                str(latest[e.id].id) if e.id in latest else None,
            )
            for e in candidates
        ],
        "weeks": [
            (
                str(w.employee_id),
                w.week_start.isoformat(),
                w.other_percent,
                w.confirmed_at.isoformat(),
                "stale" if w.confirmed_at < cutoff else "confirmed",
            )
            for w in weeks
        ],
        "unavailability": [
            (
                str(u.id),
                str(u.employee_id),
                u.start_date.isoformat(),
                u.end_date.isoformat(),
            )
            for u in unavailable
        ],
        "assignments": [
            (
                str(a.id),
                str(a.employee_id),
                a.start_date.isoformat() if a.start_date else None,
                a.due_date.isoformat(),
                a.allocation_percent,
                str(a.status),
            )
            for a in assignments
        ],
        "external_departments": [(d["department_id"], d["count"]) for d in departments],
        "holds": [
            (
                str(line.id),
                str(line.employee_id),
                line.start_date.isoformat(),
                line.end_date.isoformat(),
                line.allocation_percent,
                str(proposal.id),
                proposal.hold_expires_at.isoformat(),
                proposal.revision,
            )
            for line, proposal in holds
        ],
    }
    return candidates, claims, raw_inputs, departments, assignments


def _fingerprint(raw_inputs: dict[str, Any]) -> str:
    ordered = {
        key: sorted(value)
        if isinstance(value, list) and key != "extra_capabilities"
        else value
        for key, value in raw_inputs.items()
    }
    return hashlib.sha256(json.dumps(ordered, sort_keys=True).encode()).hexdigest()


def build(
    session: Session, principal: Principal, demand: CapacityDemand
) -> tuple[dict[str, Any], str, list[dict[str, object]]]:
    candidates, claims, raw, departments, assignments = _inputs(
        session, principal, demand
    )
    basis = get_demand_basis(principal, session, demand.id)
    basis_ids = {p.employee_id for p in basis.data}
    start = max(
        week_start(demand.window_start), date.fromisoformat(raw["current_week"])
    )
    weeks = max(0, (week_start(demand.window_end) - start).days // 7 + 9)
    week_loads = loads(
        session,
        principal.org_id,
        [e.id for e in candidates],
        start,
        weeks,
        exclude_demand_id=demand.id,
        exclude_committed_demand_id=demand.id if raw.get("change", False) else None,
    )
    assigned_ids = set(
        session.exec(
            select(CapacityAssignment.employee_id).where(
                CapacityAssignment.org_id == principal.org_id,
                col(CapacityAssignment.employee_id).in_([e.id for e in candidates]),
            )
        ).all()
    )
    schema = get_schema(session, principal.org_id)
    capability = session.exec(
        select(CapabilityDefinition).where(
            CapabilityDefinition.org_id == principal.org_id,
            CapabilityDefinition.schema_version_id == schema.id,
            CapabilityDefinition.key == demand.capability_key,
        )
    ).first()
    extras = raw.get("extra_capabilities", [])
    today = utc_now().date()
    data = {
        "demand": {
            "id": str(demand.id),
            "revision": demand.revision,
            "change": raw.get("change", False),
            "capability_key": demand.capability_key,
            "minimum_level": demand.minimum_level,
            **(
                {
                    "capability_name": demand.capability_name,
                    "extra_capabilities": extras,
                }
                if extras
                else {}
            ),
            "headcount": demand.headcount,
            "allocation_percent": demand.allocation_percent,
            "window_start": demand.window_start.isoformat(),
            "window_end": demand.window_end.isoformat(),
            "committed_start_date": demand.committed_start_date.isoformat()
            if demand.committed_start_date
            else None,
            "committed_end_date": demand.committed_end_date.isoformat()
            if demand.committed_end_date
            else None,
        },
        "candidates": [
            {
                "employee_id": str(e.id),
                "department_id": str(e.department_id),
                "level": claims.get(e.id, {}).get(demand.capability_key),
                **(
                    {
                        "extra_levels": {
                            item["key"]: claims.get(e.id, {}).get(item["key"])
                            for item in extras
                        }
                    }
                    if extras
                    else {}
                ),
                "in_basis": e.id in basis_ids,
                "newcomer": (0 <= (today - e.hire_date).days <= 365)
                if e.hire_date
                else e.id not in assigned_ids,
                "employment_status": str(e.employment_status),
                "weeks": [w.model_dump(mode="json") for w in week_loads[e.id]],
            }
            for e in candidates
        ],
        "assignments": [
            {
                "assignment_id": str(a.id),
                "employee_id": str(a.employee_id),
                "start_date": a.start_date.isoformat() if a.start_date else None,
                "counted_from": (
                    a.start_date or week_start(a.created_at.date())
                ).isoformat(),
                "due_date": a.due_date.isoformat(),
                "allocation_percent": a.allocation_percent,
            }
            for a in assignments
            if not (
                raw.get("change", False)
                and a.demand_id == demand.id
                and a.proposal_line_id is not None
            )
        ],
        "rules": {
            "fresh_days": FRESH_DAYS,
            "minimum_percent": 10,
            "levels": _level_order(capability),
            "current_week": raw["current_week"],
        },
    }
    return data, _fingerprint(raw), departments


def current_fingerprint(
    session: Session,
    principal: Principal,
    demand: CapacityDemand,
) -> str:
    _, _, raw, _, _ = _inputs(session, principal, demand)
    return _fingerprint(raw)
