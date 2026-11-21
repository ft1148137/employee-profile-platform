from collections import Counter, defaultdict
from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any, Literal, cast

from pydantic import BaseModel

from app.domains.capacity.calendar import WeekLoad, shortfalls, week_start

Verdict = Literal["feasible", "conditional", "infeasible", "insufficient_data"]
PHASED_APPROVAL = "需求方同意分阶段交付"
EXTRA_CAPABILITY_FINDINGS: dict[tuple[bool, bool | None], tuple[str, Verdict, str]] = {
    (True, False): ("required_capability_below", "infeasible", "必需能力未达标"),
    (True, None): (
        "required_capability_unknown",
        "insufficient_data",
        "必需能力未确认",
    ),
    (False, False): ("optional_capability_below", "feasible", "可选能力未达标"),
    (False, None): ("optional_capability_unknown", "feasible", "可选能力未确认"),
}


class Validation(BaseModel):
    verdict: Verdict
    findings: list[dict[str, Any]]
    summary: dict[str, Any]


def extra_capability_matches(
    demand: dict[str, Any], person: dict[str, Any]
) -> Iterator[tuple[dict[str, Any], bool | None]]:
    published = person.get("extra_levels", {})
    for capability in demand.get("extra_capabilities", []):
        level = published.get(capability["key"])
        minimum = capability["minimum_level"]
        levels = capability["levels"]
        if level is None:
            yield capability, None
        else:
            yield (
                capability,
                minimum is None
                or (
                    level in levels
                    and minimum in levels
                    and levels.index(level) >= levels.index(minimum)
                ),
            )


def validate(
    data: dict[str, Any],
    params: dict[str, Any],
    *,
    kind: str = "custom",
    phased: bool = False,
) -> Validation:
    """Validate saved inputs only; no database reads or writes."""
    demand = data["demand"]
    has_extra_capabilities = bool(demand.get("extra_capabilities"))
    primary_name = demand.get("capability_name", "主能力")
    candidates = {p["employee_id"]: p for p in data["candidates"]}
    levels = data["rules"]["levels"]
    current_week = date.fromisoformat(data["rules"]["current_week"])
    findings: list[dict[str, Any]] = []
    assignees = params["assignees"]
    conditions: list[str] = []
    newcomer_ids: set[str] = set()
    finish: date | None = None
    allocations: dict[tuple[str, date], int] = {}
    matched_extras: dict[str, list[str]] = {}

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
        if has_extra_capabilities and employee_id not in matched_extras:
            matched_extras[employee_id] = []
            unmet: dict[tuple[bool, bool | None], list[str]] = defaultdict(list)
            if person is None or person["level"] is None:
                unmet[True, None].append(primary_name)
            for capability, matches in extra_capability_matches(demand, person or {}):
                name = capability["name"]
                if matches:
                    matched_extras[employee_id].append(name)
                else:
                    unmet[capability["required"], matches].append(name)
            for status, (
                code,
                finding_level,
                message,
            ) in EXTRA_CAPABILITY_FINDINGS.items():
                if names := unmet.get(status):
                    add(
                        code,
                        finding_level,
                        f"{message}：{'、'.join(names)}",
                        employee_id=employee_id,
                    )
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
                f"主能力等级低于需求：{primary_name}"
                if has_extra_capabilities
                else "能力等级低于需求",
                employee_id=employee_id,
            )
        elif not person["in_basis"] and (
            level is not None or not has_extra_capabilities
        ):
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
            if date.fromisoformat(w["week_start"]) >= current_week
            and (employee_id, date.fromisoformat(w["week_start"])) in allocations
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
            if eid == employee_id and week >= current_week and week not in known:
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
    required = demand["headcount"]
    if demand.get("change") and finish is not None:
        committed_end = date.fromisoformat(
            demand["committed_end_date"] or demand["window_end"]
        )
        if finish < committed_end:
            add("early_finish", "conditional", "需求方同意提前结束")
        last_week = week_start(finish)
        maximum = max(Counter(start for _, start in allocations).values(), default=0)
        if 0 < maximum < required:
            required = maximum
            add("reduced_headcount", "conditional", "需求方同意减少人数")
    while week <= last_week:
        active = {
            employee_id: percent
            for (employee_id, start), percent in allocations.items()
            if start == week
        }
        covered_headcount = min(covered_headcount, len(active))
        if len(active) < required:
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
    segments: dict[str, set[tuple[str, str]]] = {}
    for assignee in assignees:
        segments.setdefault(assignee["employee_id"], set()).add(
            (assignee["start_date"], assignee["end_date"])
        )
    periods = sorted({period for parts in segments.values() for period in parts})
    coverage_end = (
        finish.isoformat() if demand.get("change") and finish else demand["window_end"]
    )
    reachable_ends: set[int] = set()
    relay = False
    # Keep all reachable phase ends; parallel work must not hide a relay chain.
    for start_text, end_text in periods:
        start, end = date.fromisoformat(start_text), date.fromisoformat(end_text)
        if start > end:
            continue
        handoff = start.toordinal() - 1 in reachable_ends
        if handoff or start_text <= demand["window_start"]:
            reachable_ends.add(end.toordinal())
            relay = relay or (handoff and end_text >= coverage_end)
    phased = phased or relay or any(len(parts) > 1 for parts in segments.values())
    # The simulator adds its condition after checking the individual phases.
    if phased and kind != "phased":
        add("phased", "conditional", PHASED_APPROVAL)
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
    affected = [
        assignment["assignment_id"]
        for assignment in data.get("assignments", [])
        if any(
            assignee["employee_id"] == assignment["employee_id"]
            and week_start(date.fromisoformat(assignee["start_date"]))
            <= week_start(date.fromisoformat(assignment["due_date"]))
            and week_start(date.fromisoformat(assignee["end_date"]))
            >= week_start(date.fromisoformat(assignment["counted_from"]))
            for assignee in assignees
        )
    ]
    codes = {finding["code"] for finding in findings}
    approvers = ["本部门经理确认答复"]
    if "reduced_headcount" in codes:
        approvers.append("需求方同意减少人数")
    if "early_finish" in codes:
        approvers.append("需求方同意提前结束")
    if "outside_window" in codes or kind == "delay":
        approvers.append("需求方接受新日期")
    if kind == "reduce_scope" or "reduced_allocation" in codes:
        approvers.append("需求方接受缩小范围")
    if kind == "phased" or phased:
        approvers.append(PHASED_APPROVAL)
    if "borrow" in codes or kind == "borrow":
        approvers.append("原部门经理和被借调员工本人同意")
    if "outside_basis" in codes:
        approvers.append("经理确认安排依据外人员")
    return Validation(
        verdict=verdict,
        findings=findings,
        summary={
            "finish_date": finish.isoformat() if finish else None,
            "covered_headcount": covered_headcount,
            "gap_person_weeks": round(gap_person_weeks, 2),
            "conditions": conditions,
            "newcomer_count": len(newcomer_ids),
            "affected_assignment_ids": affected,
            "approvers": approvers,
            **(
                {"matched_extra_capabilities": matched_extras} if matched_extras else {}
            ),
        },
    )
