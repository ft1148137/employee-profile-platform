"""Offline scheduling over saved Planning snapshots, in submission order."""

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Protocol

from app.domains.capacity.calendar import week_start
from app.domains.planning import simulator, validator

type Snapshot = dict[str, Any]
type Assignments = list[dict[str, Any]]
type Schedule = dict[str, Assignments]
type Occupancy = Counter[tuple[str, str]]

MAX_DELAY = 8


@dataclass(frozen=True)
class Role:
    key: str
    snapshot: Snapshot
    predecessors: tuple[str, ...] = ()


@dataclass(frozen=True)
class Solution:
    filled: Schedule
    unfilled: tuple[str, ...]


class SolverPort(Protocol):
    def solve(self, roles: Sequence[Role]) -> Solution: ...


def shifted(data: Snapshot, weeks: int) -> Snapshot:
    demand = dict(data["demand"])
    for key in (
        "window_start",
        "window_end",
        "committed_start_date",
        "committed_end_date",
    ):
        if demand.get(key):
            demand[key] = (
                date.fromisoformat(demand[key]) + timedelta(weeks=weeks)
            ).isoformat()
    return {**data, "demand": demand}


def delay(data: Snapshot, rows: Assignments) -> int:
    first = date.fromisoformat(min(row["start_date"] for row in rows))
    return (first - date.fromisoformat(data["demand"]["window_start"])).days // 7


def with_occupancy(data: Snapshot, used: Occupancy) -> Snapshot:
    return {
        **data,
        "candidates": [
            {
                **person,
                "weeks": [
                    {
                        **week,
                        "platform_percent": week["platform_percent"]
                        + used[person["employee_id"], week["week_start"]],
                    }
                    for week in person["weeks"]
                ],
            }
            for person in data["candidates"]
        ],
    }


def occupy(used: Occupancy, rows: Assignments) -> None:
    for row in rows:
        week = week_start(date.fromisoformat(row["start_date"]))
        end = date.fromisoformat(row["end_date"])
        while week <= end:
            used[row["employee_id"], week.isoformat()] += row["allocation_percent"]
            week += timedelta(weeks=1)


def full_plan(data: Snapshot, rows: Assignments) -> bool:
    if not rows or not 0 <= (offset := delay(data, rows)) <= MAX_DELAY:
        return False
    adjusted = shifted(data, offset)
    demand = adjusted["demand"]
    eligible = {p["employee_id"] for p in data["candidates"] if p["in_basis"]}
    periods: dict[tuple[str, str], set[str]] = {}
    for row in rows:
        person = row["employee_id"]
        start, end = row["start_date"], row["end_date"]
        people = periods.setdefault((start, end), set())
        if (
            person not in eligible
            or person in people
            or row["allocation_percent"] != demand["allocation_percent"]
            or not demand["window_start"] <= start <= end <= demand["window_end"]
            or week_start(date.fromisoformat(start)).isoformat()
            < data["rules"]["current_week"]
        ):
            return False
        people.add(person)
    spans = sorted(periods)
    if (
        spans[0][0] != demand["window_start"]
        or spans[-1][1] != demand["window_end"]
        or len(spans) > 2
        or any(len(people) != demand["headcount"] for people in periods.values())
    ):
        return False
    if len(spans) == 2 and (
        date.fromisoformat(spans[1][0])
        != date.fromisoformat(spans[0][1]) + timedelta(days=1)
        or date.fromisoformat(spans[1][0]).weekday() != 0
    ):
        return False
    return (
        validator.validate(adjusted, {"assignees": rows}, kind="phased").verdict
        == "feasible"
    )


def solution(roles: Sequence[Role], filled: Schedule) -> Solution:
    return Solution(filled, tuple(role.key for role in roles if role.key not in filled))


def sequential(
    roles: Sequence[Role], choose: Callable[[Snapshot], Assignments]
) -> Solution:
    used: Occupancy = Counter()
    filled: Schedule = {}
    for role in roles:
        if any(key not in filled for key in role.predecessors):
            continue
        base = with_occupancy(role.snapshot, used)
        start = date.fromisoformat(base["demand"]["window_start"])
        offset = max(
            (
                (date.fromisoformat(row["end_date"]) - start).days // 7 + 1
                for key in role.predecessors
                for row in filled[key]
            ),
            default=0,
        )
        if offset > MAX_DELAY:
            continue
        rows = choose(shifted(base, max(0, offset)))
        if rows and full_plan(base, rows):
            filled[role.key] = rows
            occupy(used, rows)
    return solution(roles, filled)


class RuleSolver:
    @staticmethod
    def candidates(data: Snapshot) -> list[Assignments]:
        options, _ = simulator.simulate(data)
        return [
            option["params"]["assignees"]
            for option in options
            if option["kind"] in {"direct", "newcomer", "delay", "phased"}
            and full_plan(data, option["params"]["assignees"])
        ]

    def solve(self, roles: Sequence[Role]) -> Solution:
        return sequential(
            roles,
            lambda data: min(
                self.candidates(data), key=lambda rows: delay(data, rows), default=[]
            ),
        )
