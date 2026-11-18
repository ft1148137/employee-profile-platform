"""Deterministic planning options from a saved snapshot."""

from datetime import date, timedelta
from typing import Any

from app.domains.capacity.calendar import week_start
from app.domains.planning import validator


def _weeks(start: date, end: date, current: date) -> list[date]:
    first = max(week_start(start), current)
    return [
        first + timedelta(weeks=i)
        for i in range(max(0, (week_start(end) - first).days // 7 + 1))
    ]


def _ranked(
    data: dict[str, Any], start: date, end: date
) -> list[tuple[dict[str, Any], int]]:
    demand = data["demand"]
    levels = data["rules"]["levels"]
    minimum = demand["minimum_level"]
    weeks = _weeks(start, end, date.fromisoformat(data["rules"]["current_week"]))
    ranked = []
    for person in data["candidates"]:
        level = person["level"]
        if not person["in_basis"] or person["employment_status"] != "active":
            continue
        if (
            level is not None
            and minimum is not None
            and (
                level not in levels
                or minimum not in levels
                or levels.index(level) < levels.index(minimum)
            )
        ):
            continue
        loads = {date.fromisoformat(w["week_start"]): w for w in person["weeks"]}
        available = min(
            (
                w["capacity_percent"] - w["other_percent"] - w["platform_percent"]
                if (w := loads.get(week)) is not None
                and w["status"] == "confirmed"
                and w["other_percent"] is not None
                else 0
                for week in weeks
            ),
            default=0,
        )
        ranked.append((person, available))
    return sorted(ranked, key=lambda item: (-item[1], item[0]["employee_id"]))


def _params(
    selected: list[dict[str, Any]],
    demand: dict[str, Any],
    *,
    start: date | None = None,
    end: date | None = None,
    percent: int | None = None,
) -> dict[str, Any]:
    return {
        "assignees": [
            {
                "employee_id": person["employee_id"],
                "allocation_percent": percent or demand["allocation_percent"],
                "start_date": (
                    start or date.fromisoformat(demand["window_start"])
                ).isoformat(),
                "end_date": (
                    end or date.fromisoformat(demand["window_end"])
                ).isoformat(),
            }
            for person in selected
        ]
    }


def _option(
    kind: str,
    data: dict[str, Any],
    params: dict[str, Any],
    condition: str | None = None,
) -> dict[str, Any]:
    result = validator.validate(data, params, kind=kind)
    if condition is not None:
        result.findings.append(
            {"code": kind, "level": "conditional", "message": condition}
        )
        if condition not in result.summary["conditions"]:
            result.summary["conditions"].append(condition)
        if result.verdict == "feasible":
            result.verdict = "conditional"
    return {
        "kind": kind,
        "params": params,
        "verdict": result.verdict,
        "findings": result.findings,
        "summary": result.summary,
    }


def simulate(data: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    demand = data["demand"]
    start = date.fromisoformat(demand["window_start"])
    end = date.fromisoformat(demand["window_end"])
    count = demand["headcount"]
    ranked = _ranked(data, start, end)
    direct = _option("direct", data, _params([p for p, _ in ranked[:count]], demand))
    options = [direct]
    notes = []

    newcomer = next(
        (
            p
            for p, free in ranked
            if p["newcomer"] and free >= demand["allocation_percent"]
        ),
        None,
    )
    if newcomer is None:
        notes.append("没有达到要求且有余量的新人")
    else:
        chosen = [
            newcomer,
            *(p for p, _ in ranked if p["employee_id"] != newcomer["employee_id"]),
        ]
        options.append(_option("newcomer", data, _params(chosen[:count], demand)))

    if direct["verdict"] != "feasible":
        for offset in range(1, 9):
            shift = timedelta(weeks=offset)
            adjusted = {
                **demand,
                "window_start": (start + shift).isoformat(),
                "window_end": (end + shift).isoformat(),
            }
            for field in ("committed_start_date", "committed_end_date"):
                if demand.get(field):
                    adjusted[field] = (
                        date.fromisoformat(demand[field]) + shift
                    ).isoformat()
            shifted_data = {**data, "demand": adjusted}
            shifted = _ranked(shifted_data, start + shift, end + shift)
            params = _params([p for p, _ in shifted[:count]], adjusted)
            trial = _option("delay", shifted_data, params)
            if trial["verdict"] == "feasible":
                options.append(
                    _option("delay", shifted_data, params, "需求方要接受新日期")
                )
                break
        else:
            notes.append("往后推迟 8 周仍没有可行方案")

        lower_percents = (
            sorted(
                {10, *range(demand["allocation_percent"] - 10, 9, -10)},
                reverse=True,
            )
            if demand["allocation_percent"] > 10
            else []
        )
        for percent in lower_percents:
            params = _params([p for p, _ in ranked[:count]], demand, percent=percent)
            trial = _option("reduce_scope", data, params)
            if trial["verdict"] in {"feasible", "conditional"}:
                options.append(
                    _option("reduce_scope", data, params, "需求方要同意缩小范围")
                )
                break
        else:
            for reduced_count in range(count - 1, 0, -1):
                adjusted = {**demand, "headcount": reduced_count}
                adjusted_data = {**data, "demand": adjusted}
                params = _params([p for p, _ in ranked[:reduced_count]], adjusted)
                if (
                    _option("reduce_scope", adjusted_data, params)["verdict"]
                    == "feasible"
                ):
                    options.append(
                        _option(
                            "reduce_scope",
                            adjusted_data,
                            params,
                            "需求方要同意缩小范围",
                        )
                    )
                    break

    span = (week_start(end) - week_start(start)).days // 7 + 1
    if span >= 2:
        middle = week_start(start) + timedelta(weeks=span // 2)
        parts = [(start, middle - timedelta(days=1)), (middle, end)]
        combined: list[dict[str, Any]] = []
        for part_start, part_end in parts:
            adjusted = {
                **demand,
                "window_start": part_start.isoformat(),
                "window_end": part_end.isoformat(),
            }
            part_data = {**data, "demand": adjusted}
            selected = [p for p, _ in _ranked(part_data, part_start, part_end)[:count]]
            params = _params(selected, adjusted)
            if _option("phased", part_data, params)["verdict"] != "feasible":
                break
            combined.extend(params["assignees"])
        else:
            options.append(
                _option(
                    "phased", data, {"assignees": combined}, "分两段交付，需求方要同意"
                )
            )
    return options, notes


def borrow(data: dict[str, Any], departments: list[dict[str, Any]]) -> dict[str, Any]:
    demand = data["demand"]
    start = date.fromisoformat(demand["window_start"])
    end = date.fromisoformat(demand["window_end"])
    condition = "需原部门经理同意并安排人员，且被借调员工本人同意"
    return {
        "kind": "borrow",
        "params": {
            "departments": [
                {
                    "department_id": d["department_id"],
                    "department_name": d["department_name"],
                    "count": d["count"] if d["count"] >= 3 else None,
                    **({"note": "人数较少"} if d["count"] < 3 else {}),
                }
                for d in departments
            ]
        },
        "verdict": "conditional",
        "findings": [{"code": "borrow", "level": "conditional", "message": condition}],
        "summary": {
            "finish_date": None,
            "covered_headcount": 0,
            "gap_person_weeks": len(_weeks(start, end, week_start(start)))
            * demand["headcount"]
            * demand["allocation_percent"]
            / 100,
            "conditions": [condition],
            "newcomer_count": 0,
            "affected_assignment_ids": [],
            "approvers": [
                "本部门经理确认答复",
                "原部门经理和被借调员工本人同意",
            ],
        },
    }
