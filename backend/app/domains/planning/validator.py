from datetime import date, timedelta
from typing import Any, Literal, cast

from pydantic import BaseModel

from app.domains.capacity.calendar import WeekLoad, shortfalls, week_start

Verdict = Literal["feasible", "conditional", "infeasible", "insufficient_data"]


class Validation(BaseModel):
    verdict: Verdict
    findings: list[dict[str, Any]]
    summary: dict[str, Any]


def validate(data: dict[str, Any], params: dict[str, Any]) -> Validation:
    """Validate saved inputs only; no database reads or writes."""
    demand = data["demand"]
    candidates = {p["employee_id"]: p for p in data["candidates"]}
    levels = data["rules"]["levels"]
    findings: list[dict[str, Any]] = []
    assignees = params["assignees"]
    conditions: list[str] = []
    newcomer_ids: set[str] = set()
    finish: date | None = None
    allocations: dict[tuple[str, date], int] = {}

    def add(
        code: str,
        level: Verdict,
        message: str,
        *,
        employee_id: str | None = None,
        week: date | None = None,
    ) -> None:
        row: dict[str, Any] = {"code": code, "level": level, "message": message}
        if employee_id is not None:
            row["employee_id"] = employee_id
        if week is not None:
            row["week_start"] = week.isoformat()
        findings.append(row)
        if level == "conditional" and message not in conditions:
            conditions.append(message)

    for assignee in assignees:
        employee_id = assignee["employee_id"]
        start = date.fromisoformat(assignee["start_date"])
        end = date.fromisoformat(assignee["end_date"])
        percent = assignee["allocation_percent"]
        if start > end:
            add(
                "invalid_dates",
                "infeasible",
                "开始日期不能晚于结束日期",
                employee_id=employee_id,
            )
            continue
        finish = max(finish, end) if finish else end
        if (
            start < date.fromisoformat(demand["window_start"])
            or end > date.fromisoformat(demand["window_end"])
            or (
                demand.get("committed_start_date")
                and start < date.fromisoformat(demand["committed_start_date"])
            )
            or (
                demand["committed_end_date"]
                and end > date.fromisoformat(demand["committed_end_date"])
            )
        ):
            add(
                "outside_window",
                "conditional",
                "需求方要接受新日期",
                employee_id=employee_id,
            )
        if percent < demand["allocation_percent"]:
            add(
                "reduced_allocation",
                "conditional",
                "需求方要同意缩小投入比例",
                employee_id=employee_id,
            )
        week = week_start(start)
        while week <= week_start(end):
            allocations[(employee_id, week)] = (
                allocations.get((employee_id, week), 0) + percent
            )
            week += timedelta(days=7)
        person = candidates.get(employee_id)
        if person is None:
            add(
                "borrow",
                "conditional",
                "借调需原部门经理和本人同意",
                employee_id=employee_id,
            )
            continue
        if person["newcomer"]:
            newcomer_ids.add(employee_id)
        if person["employment_status"] != "active":
            add("inactive", "infeasible", "员工已离职", employee_id=employee_id)
        level = person["level"]
        minimum = demand["minimum_level"]
        if (
            level is not None
            and minimum is not None
            and (
                level not in levels
                or minimum not in levels
                or levels.index(level) < levels.index(minimum)
            )
        ):
            add(
                "level_too_low",
                "infeasible",
                "能力等级低于需求",
                employee_id=employee_id,
            )
        elif not person["in_basis"]:
            add(
                "outside_basis",
                "conditional",
                "画像未记录这项能力，按经理判断",
                employee_id=employee_id,
            )
    for employee_id, person in candidates.items():
        selected_weeks = [
            WeekLoad.model_validate(w)
            for w in person["weeks"]
            if (employee_id, date.fromisoformat(w["week_start"])) in allocations
        ]
        for load in selected_weeks:
            percent = allocations[(employee_id, load.week_start)]
            unconfirmed, over = shortfalls([load], percent)
            for week in unconfirmed:
                add(
                    "unconfirmed",
                    "insufficient_data",
                    "该周容量未确认或需重新确认",
                    employee_id=employee_id,
                    week=week,
                )
            for week, excess in over:
                add(
                    "over_capacity",
                    "infeasible",
                    f"该周超出容量 {excess}%",
                    employee_id=employee_id,
                    week=week,
                )
        known = {w.week_start for w in selected_weeks}
        for eid, week in allocations:
            if eid == employee_id and week not in known:
                add(
                    "unconfirmed",
                    "insufficient_data",
                    "该周容量没有快照数据",
                    employee_id=employee_id,
                    week=week,
                )

    gap_person_weeks = 0.0
    covered_headcount = demand["headcount"]
    week = week_start(date.fromisoformat(demand["window_start"]))
    last_week = week_start(date.fromisoformat(demand["window_end"]))
    while week <= last_week:
        active = {
            employee_id: percent
            for (employee_id, start), percent in allocations.items()
            if start == week
        }
        covered_headcount = min(covered_headcount, len(active))
        if len(active) < demand["headcount"]:
            add("headcount_gap", "infeasible", "该周覆盖人数少于需求", week=week)
        gap_person_weeks += (
            max(
                demand["headcount"] * demand["allocation_percent"]
                - sum(
                    min(percent, demand["allocation_percent"])
                    for percent in active.values()
                ),
                0,
            )
            / 100
        )
        week += timedelta(days=7)
    severity = {
        "feasible": 0,
        "conditional": 1,
        "insufficient_data": 2,
        "infeasible": 3,
    }
    verdict = cast(
        Verdict,
        max(
            (f["level"] for f in findings), key=severity.__getitem__, default="feasible"
        ),
    )
    return Validation(
        verdict=verdict,
        findings=findings,
        summary={
            "finish_date": finish.isoformat() if finish else None,
            "covered_headcount": covered_headcount,
            "gap_person_weeks": gap_person_weeks,
            "conditions": conditions,
            "newcomer_count": len(newcomer_ids),
            "affected_assignment_ids": [],
        },
    )
