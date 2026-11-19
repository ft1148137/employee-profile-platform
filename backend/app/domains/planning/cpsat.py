"""CP-SAT scheduling over authorized Planning snapshots."""

from collections import defaultdict
from collections.abc import Sequence
from datetime import date, timedelta
from time import perf_counter
from typing import Any

from app.domains.capacity.calendar import week_start
from app.domains.planning.simulator import ranked_candidates
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


def _collapse_identical_phases(rows: Assignments, headcount: int) -> Assignments:
    # Selected rows are ordered by phase and then by employee.
    first, second = rows[:headcount], rows[headcount:]
    if second and [(r["employee_id"], r["allocation_percent"]) for r in first] == [
        (r["employee_id"], r["allocation_percent"]) for r in second
    ]:
        return [{**row, "end_date": second[0]["end_date"]} for row in first]
    return rows


class CpSatSolver:
    def solve(self, roles: Sequence[Role]) -> Solution:
        from ortools.sat.python import cp_model

        deadline = perf_counter() + SOLVE_TIMEOUT_SECONDS
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

        for role in roles:
            demand = role.snapshot["demand"]
            committed_start = demand.get("committed_start_date")
            committed_end = demand.get("committed_end_date")
            newcomer_rows: list[cp_model.IntVar] = []
            for offset in range(MAX_DELAY + 1):
                if perf_counter() >= deadline:
                    raise SolverIncomplete("CP-SAT time budget exhausted")
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
                for first, last in periods:
                    people: list[cp_model.IntVar] = []
                    for person, free in ranked_candidates(data, first, last):
                        if free < percent:
                            continue
                        eid = person["employee_id"]
                        take = model.new_bool_var(f"{role.key}:{first}:{eid}")
                        people.append(take)
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
                    model.add(sum(people) == d["headcount"] * picked)
            model.add(sum(selected[role.key]) <= 1)
            for pred in role.predecessors:
                model.add(sum(selected[pred]) >= sum(selected[role.key]))
                for picked in selected[role.key]:
                    model.add(sum(starts[role.key]) > sum(ends[pred])).only_enforce_if(
                        picked
                    )
            if newcomer_rows:
                flag = model.new_bool_var(f"newcomer:{role.key}")
                model.add_max_equality(flag, newcomer_rows)
                newcomer_roles.append(flag)

        for key, terms in loads.items():
            model.add(sum(terms) <= limits[key])
        n = len(roles)
        model.maximize(
            (MAX_DELAY * n + 1)
            * (n + 1)
            * sum(v for group in selected.values() for v in group)
            - (n + 1) * sum(delays)
            + sum(newcomer_roles)
        )
        solver = cp_model.CpSolver()
        solver.parameters.num_search_workers = 1
        solver.parameters.random_seed = 0
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
                filled[role.key] = _collapse_identical_phases(
                    sorted(
                        assigned,
                        key=lambda row: (row["start_date"], row["employee_id"]),
                    ),
                    role.snapshot["demand"]["headcount"],
                )
        if perf_counter() >= deadline:
            raise SolverIncomplete("CP-SAT time budget exhausted")
        return solution(roles, filled)
