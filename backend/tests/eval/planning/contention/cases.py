"""Expand compact synthetic inputs and load replay snapshots."""

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

from app.domains.planning.solver import Role

HERE = Path(__file__).resolve().parent
MONDAY = date(2026, 11, 16)
LEVELS = ["junior", "senior"]


def synthetic(case: dict[str, Any]) -> list[Role]:
    def day(offset: int) -> str:
        return (MONDAY + timedelta(days=offset)).isoformat()

    roles = []
    for project, specs in enumerate(case["projects"], 1):
        for index, spec in enumerate(specs, 1):
            skills, start, duration, percent, count, before = spec
            main, _, extra = skills.partition("+")
            demand = {
                "capability_key": main,
                "minimum_level": "senior",
                "headcount": count,
                "allocation_percent": percent,
                "window_start": day(7 * start),
                "window_end": day(7 * (start + duration) - 1),
                "committed_end_date": None,
            }
            if extra:
                demand["extra_capabilities"] = [
                    {
                        "key": extra,
                        "name": extra,
                        "levels": LEVELS,
                        "minimum_level": "senior",
                        "required": True,
                    }
                ]
            candidates = []
            for person in range(1, case["people"] + 1):
                unavailable = case["unavailable"].get(person, [])
                candidates.append(
                    {
                        "employee_id": f"{person:02}",
                        "department_id": "01",
                        "employment_status": "active",
                        "in_basis": True,
                        "newcomer": person in case["newcomers"],
                        "level": "senior"
                        if person in case["skills"][main]
                        else "junior",
                        "extra_levels": {
                            extra: "senior"
                            if person in case["skills"].get(extra, [])
                            else None
                        }
                        if extra
                        else {},
                        "weeks": [
                            {
                                "week_start": day(7 * week),
                                "capacity_percent": 100
                                - 20
                                * len(
                                    set(range(7 * week, 7 * week + 5)).intersection(
                                        unavailable
                                    )
                                ),
                                "other_percent": case["other"].get(person, 40),
                                "platform_percent": 0,
                                "status": "confirmed",
                                "confirmed_at": None,
                                "legacy_start": False,
                            }
                            for week in range(start, start + duration + 8)
                        ],
                    }
                )
            roles.append(
                Role(
                    f"{project}.{index}",
                    {
                        "demand": demand,
                        "candidates": candidates,
                        "rules": {"current_week": day(0), "levels": LEVELS},
                    },
                    (f"{project}.{before}",) if before else (),
                )
            )
    # Match role creation order: projects are submitted as batches.
    return roles


def load_cases() -> dict[str, list[Role]]:
    cases = {
        case["id"]: synthetic(case)
        for case in yaml.safe_load((HERE / "synthetic.yaml").read_text())
    }
    cases.update(
        (path.stem, [Role("1", json.loads(path.read_text()))])
        for path in sorted(HERE.glob("replay-*.json"))
    )
    return cases
