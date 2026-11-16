import hashlib
import json
from datetime import date, timedelta
from typing import Any
from uuid import UUID

from sqlmodel import Session, col, select

from app.authz.policies import employee_scope_clause
from app.authz.principal import Principal
from app.domains.base import utc_now
from app.domains.capability.models import CapabilityDefinition
from app.domains.capability.service import get_schema
from app.domains.capacity.calendar import FRESH_DAYS, loads, this_week, week_start
from app.domains.capacity.demand import get_demand_basis
from app.domains.capacity.models import (
    AssignmentStatus,
    CapacityAssignment,
    CapacityDemand,
    CapacityUnavailability,
    CapacityWeek,
)
from app.domains.org.models import Employee, EmploymentStatus
from app.domains.org.service import managed_scope
from app.domains.profile.models import ProfileVersion


def _inputs(
    session: Session,
    principal: Principal,
    demand: CapacityDemand,
) -> tuple[list[Employee], dict[UUID, tuple[str | None, UUID | None]], dict[str, Any]]:
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
    claims: dict[UUID, tuple[str | None, UUID | None]] = {}
    for eid, version in latest.items():
        raw = version.snapshot.get("claims", [])
        if isinstance(raw, list):
            for claim in raw:
                if (
                    isinstance(claim, dict)
                    and keys.get(str(claim.get("capability_definition_id")))
                    == demand.capability_key
                ):
                    level = claim.get("level")
                    claims[eid] = (
                        level if isinstance(level, str) else None,
                        version.id,
                    )
                    break
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
    raw_inputs: dict[str, Any] = {
        "current_week": current_week.isoformat(),
        "revision": demand.revision,
        "candidates": [
            (
                str(e.id),
                str(e.department_id),
                str(e.employment_status),
                str(claims.get(e.id, (None, None))[1]),
            )
            for e in candidates
        ],
        "weeks": [
            (
                str(w.employee_id),
                w.week_start.isoformat(),
                w.other_percent,
                w.confirmed_at.isoformat(),
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
    }
    return candidates, claims, raw_inputs


def _fingerprint(raw_inputs: dict[str, Any]) -> str:
    ordered = {
        key: sorted(value) if isinstance(value, list) else value
        for key, value in raw_inputs.items()
    }
    return hashlib.sha256(json.dumps(ordered, sort_keys=True).encode()).hexdigest()


def build(
    session: Session, principal: Principal, demand: CapacityDemand
) -> tuple[dict[str, Any], str]:
    candidates, claims, raw = _inputs(session, principal, demand)
    basis = get_demand_basis(principal, session, demand.id)
    basis_ids = {p.employee_id for p in basis.data}
    start = max(
        week_start(demand.window_start), date.fromisoformat(raw["current_week"])
    )
    weeks = max(0, (week_start(demand.window_end) - start).days // 7 + 9)
    week_loads = loads(
        session, principal.org_id, [e.id for e in candidates], start, weeks
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
    rubric = capability.level_rubric if capability else None
    raw_levels = rubric.get("levels", []) if rubric else []
    levels = (
        [str(v["key"]) for v in raw_levels if isinstance(v, dict) and "key" in v]
        if isinstance(raw_levels, list)
        else []
    )
    today = utc_now().date()
    data = {
        "demand": {
            "id": str(demand.id),
            "revision": demand.revision,
            "capability_key": demand.capability_key,
            "minimum_level": demand.minimum_level,
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
                "level": claims.get(e.id, (None, None))[0],
                "in_basis": e.id in basis_ids,
                "newcomer": (0 <= (today - e.hire_date).days <= 365)
                if e.hire_date
                else e.id not in assigned_ids,
                "employment_status": str(e.employment_status),
                "weeks": [w.model_dump(mode="json") for w in week_loads[e.id]],
            }
            for e in candidates
        ],
        "rules": {
            "fresh_days": FRESH_DAYS,
            "minimum_percent": 10,
            "levels": levels,
            "current_week": raw["current_week"],
        },
    }
    return data, _fingerprint(raw)


def current_fingerprint(
    session: Session,
    principal: Principal,
    demand: CapacityDemand,
) -> str:
    _, _, raw = _inputs(session, principal, demand)
    return _fingerprint(raw)
