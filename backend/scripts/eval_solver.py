"""Compare offline rule, Pi Agent + validator, and CP-SAT scheduling."""

import argparse
import json
from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import date, timedelta
from time import perf_counter
from typing import Any, cast

from ortools.sat.python import cp_model
from pydantic import JsonValue, SecretStr

from app.core.config import settings
from app.domains.capacity.calendar import week_start
from app.domains.model import pi_client
from app.domains.model.client import ModelRateLimited, ModelUnavailable
from app.domains.planning.simulator import ranked_candidates
from app.domains.planning.solver import (
    MAX_DELAY,
    Assignments,
    Occupancy,
    Role,
    RuleSolver,
    Schedule,
    Snapshot,
    Solution,
    SolverPort,
    delay,
    full_plan,
    occupy,
    sequential,
    shifted,
    solution,
    with_occupancy,
)

MODEL = "gpt-6.1-sol"


class CpSatSolver:
    def solve(self, roles: Sequence[Role]) -> Solution:
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
            newcomer_rows: list[cp_model.IntVar] = []
            for offset in range(MAX_DELAY + 1):
                data = shifted(role.snapshot, offset)
                d = data["demand"]
                start, end = map(
                    date.fromisoformat, (d["window_start"], d["window_end"])
                )
                if (
                    week_start(start).isoformat() < data["rules"]["current_week"]
                    or start.isoformat()
                    < (d.get("committed_start_date") or d["window_start"])
                    or end.isoformat()
                    > (d.get("committed_end_date") or d["window_end"])
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
        solver.parameters.max_time_in_seconds = 10
        status = solver.solve(model)
        if status != cp_model.OPTIMAL:
            raise RuntimeError(
                f"CP-SAT did not prove optimality: {solver.status_name(status)}"
            )
        filled: Schedule = {}
        for role in roles:
            if assigned := [
                row for take, row in rows[role.key] if solver.boolean_value(take)
            ]:
                filled[role.key] = sorted(
                    assigned, key=lambda row: (row["start_date"], row["employee_id"])
                )
        return solution(roles, filled)


class AgentSolver:
    """One real Pi turn per role. Offer simulated plans and validate any revision."""

    def solve(self, roles: Sequence[Role]) -> Solution:
        if not settings.CHATGPT_LOCAL_AUTH or settings.FASTAPI_ENV != "development":
            raise ValueError(
                "The Agent eval requires the local Pi development service."
            )
        return sequential(roles, self.choose)

    def choose(self, data: Snapshot) -> Assignments:
        options: list[Assignments] = []

        def call(name: str, arguments: dict[str, JsonValue]) -> JsonValue:
            if name == "simulate_plan":
                options.clear()
                options.extend(RuleSolver.candidates(data))
                return cast(
                    "JsonValue", {str(i): plan for i, plan in enumerate(options, 1)}
                )
            rows = arguments.get("assignees")
            if isinstance(rows, list) and all(
                isinstance(row, dict)
                and all(
                    isinstance(row.get(k), str)
                    for k in ("employee_id", "start_date", "end_date")
                )
                and type(row.get("allocation_percent")) is int
                for row in rows
            ):
                try:
                    if full_plan(data, cast("Assignments", rows)):
                        options.append(cast("Assignments", rows))
                        return len(options)
                except ValueError:
                    pass
            return {"error": "This assignment fails validation."}

        turn = pi_client.run_turn(
            pi_client.PiModelConfig(
                provider="openai-codex",
                api="openai-codex-responses",
                id=MODEL,
                baseUrl="https://chatgpt.com/backend-api",
                api_key=SecretStr("local-codex"),
                thinkingLevel="low",
            ),
            mode="chat",
            system_prompt=(
                "You schedule one role at a time on a weekly capacity calendar. "
                "First call simulate_plan. Optionally submit a revised full plan to validate_plan. "
                "Prefer completing the role with the least delay. Return solely the integer ID "
                "of a validated plan, or 0 if no full plan is possible."
            ),
            input=json.dumps(data, ensure_ascii=False),
            tools=[
                pi_client.ToolDefinition(
                    name="simulate_plan",
                    description="Return numbered valid rule plans.",
                    parameters={"type": "object", "properties": {}},
                ),
                pi_client.ToolDefinition(
                    name="validate_plan",
                    description="Return an ID if the revised plan passes.",
                    parameters={
                        "type": "object",
                        "required": ["assignees"],
                        "properties": {
                            "assignees": {"type": "array", "items": {"type": "object"}},
                        },
                    },
                ),
            ],
            on_tool_call=call,
        )
        text = turn.text.strip()
        return (
            options[int(text) - 1]
            if text.isascii()
            and text.isdecimal()
            and len(text) < 4
            and 0 < int(text) <= len(options)
            else []
        )


def metrics(roles: Sequence[Role], result: Solution) -> dict[str, int]:
    filled = result.filled
    stats: Counter[str] = Counter(filled=0, delay=0, newcomer=0, violations=0)
    for role in roles:
        rows = filled.get(role.key)
        if not rows:
            continue
        other: Occupancy = Counter()
        for key, assignments in filled.items():
            if key != role.key:
                occupy(other, assignments)
        first = min(row["start_date"] for row in rows)
        if not full_plan(with_occupancy(role.snapshot, other), rows) or any(
            pred not in filled or max(row["end_date"] for row in filled[pred]) >= first
            for pred in role.predecessors
        ):
            stats["violations"] += 1
            continue
        stats["filled"] += 1
        stats["delay"] += 5 * delay(role.snapshot, rows)
        newbies = {
            p["employee_id"] for p in role.snapshot["candidates"] if p["newcomer"]
        }
        stats["newcomer"] += any(row["employee_id"] in newbies for row in rows)
    return dict(stats)


def evaluate(solver: SolverPort, roles: Sequence[Role]) -> dict[str, Any]:
    started = perf_counter()
    result = solver.solve(roles)
    elapsed = perf_counter() - started
    return {**metrics(roles, result), "seconds": round(elapsed, 4)}


def main() -> None:
    from tests.eval.planning.contention.cases import load_cases

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", action="store_true")
    parser.add_argument("--case")
    args = parser.parse_args()
    missing: str | None = None
    cases = load_cases()
    if args.case and args.case not in cases:
        parser.error(f"Unknown case: {args.case}")
    for key, roles in cases.items():
        if args.case and key != args.case:
            continue
        result: dict[str, Any] = {
            "case": key,
            "roles": len(roles),
            "rule": evaluate(RuleSolver(), roles),
            "cp_sat": evaluate(CpSatSolver(), roles),
        }
        if args.agent and missing is None:
            try:
                result["agent"] = evaluate(AgentSolver(), roles)
            except (ModelRateLimited, ModelUnavailable) as error:
                missing = str(getattr(error, "code", type(error).__name__))
        if args.agent and missing is not None:
            result["agent_unavailable"] = missing
        print(json.dumps(result), flush=True)  # noqa: T201


if __name__ == "__main__":
    main()
