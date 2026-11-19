"""CP-SAT scheduling over authorized Planning snapshots."""

from collections import defaultdict
from collections.abc import Sequence
from datetime import date, timedelta
from functools import partial
from time import perf_counter
from typing import Any

from app.domains.capacity.calendar import week_start
from app.domains.planning.simulator import ranked_candidates_with_matches
from app.domains.planning.solver import (
    MAX_DELAY,
    Assignments,
    Role,
    Schedule,
    Solution,
    shifted,
    solution,
)

SOLVE_TIMEOUT_SECONDS = 10


class SolverIncomplete(RuntimeError):
    """The solver could not prove optimality within its budget."""


def _check_deadline(deadline: float) -> None:
    if perf_counter() >= deadline:
        raise SolverIncomplete("CP-SAT time budget exhausted")


def _collapse_identical_phases(rows: Assignments) -> Assignments:
    merged: Assignments = []
    previous: dict[str, dict[str, Any]] = {}
    for row in sorted(rows, key=lambda row: (row["start_date"], row["employee_id"])):
        employee = row["employee_id"]
        prior = previous.get(employee)
        if (
            prior is not None
            and prior["allocation_percent"] == row["allocation_percent"]
            and date.fromisoformat(prior["end_date"]) + timedelta(days=1)
            == date.fromisoformat(row["start_date"])
        ):
            prior["end_date"] = row["end_date"]
        else:
            previous[employee] = row.copy()
            merged.append(previous[employee])
    return merged


class CpSatSolver:
    def solve(self, roles: Sequence[Role]) -> Solution:
        from ortools.sat.python import cp_model

        deadline = perf_counter() + SOLVE_TIMEOUT_SECONDS
        check_deadline = partial(_check_deadline, deadline)
        model = cp_model.CpModel()
        selected: dict[str, list[cp_model.IntVar]] = defaultdict(list)
        rows: dict[str, list[tuple[cp_model.IntVar, dict[str, Any]]]] = defaultdict(
            list
        )
        starts: dict[str, list[cp_model.LinearExpr]] = defaultdict(list)
        ends: dict[str, list[cp_model.LinearExpr]] = defaultdict(list)
        loads: dict[tuple[str, str], list[cp_model.LinearExpr]] = defaultdict(list)
        limits: dict[tuple[str, str], int] = {}
        delays: list[cp_model.LinearExpr] = []
        newcomer_roles: list[cp_model.IntVar] = []
        stays: list[cp_model.IntVar] = []
        preferences: list[cp_model.LinearExpr] = []
        max_stays = max_preference = 0

        for role in roles:
            check_deadline()
            demand = role.snapshot["demand"]
            headcount = demand["headcount"]
            availability_weight = 1 + sum(
                not capability["required"]
                for capability in demand.get("extra_capabilities", [])
            )
            role_max_preference = 0
            prior_stay_count = len(stays)
            committed_start = demand.get("committed_start_date")
            committed_end = demand.get("committed_end_date")
            newcomer_rows: list[cp_model.IntVar] = []
            for offset in range(MAX_DELAY + 1):
                check_deadline()
                data = shifted(role.snapshot, offset)
                d = data["demand"]
                start, end = map(
                    date.fromisoformat, (d["window_start"], d["window_end"])
                )
                if (
                    week_start(start).isoformat() < data["rules"]["current_week"]
                    or (committed_start and d["window_start"] < committed_start)
                    or (committed_end and d["window_end"] > committed_end)
                    # An earlier finish on a change requires requester approval.
                    or (
                        committed_end
                        and demand.get("change")
                        and d["window_end"] < committed_end
                    )
                ):
                    continue
                picked = model.new_bool_var(f"{role.key}:{offset}")
                selected[role.key].append(picked)
                starts[role.key].append(start.toordinal() * picked)
                ends[role.key].append(end.toordinal() * picked)
                delays.append(offset * picked)
                span = (week_start(end) - week_start(start)).days // 7 + 1
                middle = week_start(start) + timedelta(weeks=span // 2)
                periods = (
                    [(start, middle - timedelta(days=1)), (middle, end)]
                    if span >= 2
                    else [(start, end)]
                )
                percent = d["allocation_percent"]
                previous_people: dict[str, cp_model.IntVar] = {}
                offset_max_preference = 0
                for first, last in periods:
                    check_deadline()
                    people: dict[str, cp_model.IntVar] = {}
                    period_max_preference = 0
                    for person, free, matches in ranked_candidates_with_matches(
                        data, first, last, check_deadline=check_deadline
                    ):
                        check_deadline()
                        if free < percent:
                            continue
                        eid = person["employee_id"]
                        take = model.new_bool_var(f"{role.key}:{first}:{eid}")
                        people[eid] = take
                        score = (free - percent) * availability_weight + matches
                        if score:
                            preferences.append(score * take)
                            period_max_preference = max(period_max_preference, score)
                        if (previous := previous_people.get(eid)) is not None:
                            stay = model.new_bool_var(f"stay:{role.key}:{offset}:{eid}")
                            model.add(stay <= previous)
                            model.add(stay <= take)
                            stays.append(stay)
                        if person["newcomer"]:
                            newcomer_rows.append(take)
                        rows[role.key].append(
                            (
                                take,
                                {
                                    "employee_id": eid,
                                    "allocation_percent": percent,
                                    "start_date": first.isoformat(),
                                    "end_date": last.isoformat(),
                                },
                            )
                        )
                        for week in person["weeks"]:
                            check_deadline()
                            when = week["week_start"]
                            if (
                                week_start(first).isoformat()
                                <= when
                                <= last.isoformat()
                            ):
                                key = (eid, when)
                                available = (
                                    week["capacity_percent"]
                                    - week["other_percent"]
                                    - week["platform_percent"]
                                )
                                limits[key] = min(limits.get(key, available), available)
                                loads[key].append(percent * take)
                    check_deadline()
                    model.add(sum(people.values()) == headcount * picked)
                    previous_people = people
                    offset_max_preference += headcount * period_max_preference
                role_max_preference = max(role_max_preference, offset_max_preference)
            max_preference += role_max_preference
            if len(stays) > prior_stay_count:
                max_stays += headcount
            check_deadline()
            model.add(sum(selected[role.key]) <= 1)
            for pred in role.predecessors:
                check_deadline()
                model.add(sum(selected[pred]) >= sum(selected[role.key]))
                for picked in selected[role.key]:
                    check_deadline()
                    model.add(sum(starts[role.key]) > sum(ends[pred])).only_enforce_if(
                        picked
                    )
            if newcomer_rows:
                flag = model.new_bool_var(f"newcomer:{role.key}")
                model.add_max_equality(flag, newcomer_rows)
                newcomer_roles.append(flag)

        for key, terms in loads.items():
            check_deadline()
            model.add(sum(terms) <= limits[key])
        check_deadline()
        n = len(roles)
        # Only one offset can contribute per role. Each tier outweighs all lower tiers.
        stay_weight = max_preference + 1
        newcomer_weight = (max_stays + 1) * stay_weight
        model.maximize(
            newcomer_weight
            * (
                (MAX_DELAY * n + 1)
                * (n + 1)
                * sum(v for group in selected.values() for v in group)
                - (n + 1) * sum(delays)
                + sum(newcomer_roles)
            )
            + stay_weight * sum(stays)
            + sum(preferences)
        )
        solver = cp_model.CpSolver()
        solver.parameters.num_search_workers = 1
        solver.parameters.random_seed = 0
        # Use the Boolean capacity and continuity constraints to tighten the bound.
        solver.parameters.linearization_level = 2
        remaining = deadline - perf_counter()
        if remaining <= 0:
            raise SolverIncomplete("CP-SAT time budget exhausted")
        solver.parameters.max_time_in_seconds = remaining
        status = solver.solve(model)
        if status != cp_model.OPTIMAL:
            raise SolverIncomplete(
                f"CP-SAT did not prove optimality: {solver.status_name(status)}"
            )
        filled: Schedule = {}
        for role in roles:
            if assigned := [
                row for take, row in rows[role.key] if solver.boolean_value(take)
            ]:
                filled[role.key] = _collapse_identical_phases(assigned)
        check_deadline()
        return solution(roles, filled)
