"""Compare offline rule, Pi Agent + validator, and CP-SAT scheduling."""

import argparse
import json
from collections import Counter
from collections.abc import Sequence
from time import perf_counter
from typing import Any, cast

from pydantic import JsonValue, SecretStr

from app.core.config import settings
from app.domains.model import pi_client
from app.domains.model.client import ModelRateLimited, ModelUnavailable
from app.domains.planning.cpsat import CpSatSolver
from app.domains.planning.solver import (
    Assignments,
    Occupancy,
    Role,
    RuleSolver,
    Snapshot,
    Solution,
    SolverPort,
    delay,
    full_plan,
    occupy,
    sequential,
    with_occupancy,
)

MODEL = "gpt-6.1-sol"


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
