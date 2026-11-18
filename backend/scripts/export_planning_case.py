"""Export a saved Planning snapshot as an offline, anonymous replay input."""

import argparse
import json
from datetime import date
from pathlib import Path
from uuid import UUID

from sqlmodel import Session, select

from app.domains.capacity.calendar import WeekLoad
from app.domains.planning.models import PlanningSnapshot
from app.domains.planning.solver import Snapshot


def anonymize(data: Snapshot) -> Snapshot:
    def day(value: str | None) -> str | None:
        return date.fromisoformat(value).isoformat() if value is not None else None

    demand = data["demand"]
    extras = demand.get("extra_capabilities", [])
    levels = data["rules"]["levels"]
    level_ids = {key: str(i) for i, key in enumerate(levels, 1)}
    people = sorted(data["candidates"], key=lambda row: row["employee_id"])
    departments = {
        key: f"{i:03}"
        for i, key in enumerate(sorted({row["department_id"] for row in people}), 1)
    }
    anonymous_extras = []
    extra_level_ids = {}
    for i, extra in enumerate(extras, 1):
        key = extra["key"]
        alias = str(i)
        order = {value: str(n) for n, value in enumerate(extra["levels"], 1)}
        extra_level_ids[key] = (alias, order)
        anonymous_extras.append(
            {
                "key": alias,
                "name": alias,
                "levels": list(order.values()),
                "minimum_level": order.get(extra["minimum_level"], "0")
                if extra["minimum_level"] is not None
                else None,
                "required": bool(extra["required"]),
            }
        )
    return {
        "demand": {
            "capability_key": "0",
            "headcount": int(demand["headcount"]),
            "allocation_percent": int(demand["allocation_percent"]),
            "minimum_level": level_ids.get(demand["minimum_level"], "0")
            if demand["minimum_level"] is not None
            else None,
            "extra_capabilities": anonymous_extras,
            "change": bool(demand.get("change")),
            **{
                key: day(demand.get(key))
                for key in (
                    "window_start",
                    "window_end",
                    "committed_start_date",
                    "committed_end_date",
                )
            },
        },
        "candidates": [
            {
                "employee_id": f"{i:03}",
                "department_id": departments[person["department_id"]],
                "level": level_ids.get(person["level"], "0")
                if person["level"] is not None
                else None,
                "extra_levels": {
                    alias: order.get(value, "0")
                    if (value := person.get("extra_levels", {}).get(key)) is not None
                    else None
                    for key, (alias, order) in extra_level_ids.items()
                },
                "newcomer": bool(person["newcomer"]),
                "in_basis": bool(person["in_basis"]),
                "employment_status": "active"
                if person["employment_status"] == "active"
                else "inactive",
                "weeks": [
                    {
                        **WeekLoad.model_validate(w).model_dump(
                            mode="json",
                            exclude={"confirmed_at", "holds"},
                        ),
                        "confirmed_at": None,
                    }
                    for w in person["weeks"]
                ],
            }
            for i, person in enumerate(people, 1)
        ],
        "rules": {
            "levels": list(level_ids.values()),
            "current_week": day(data["rules"]["current_week"]),
        },
    }


def export_saved(db: Session, org_id: UUID, snapshot_id: UUID, output: Path) -> None:
    snapshot = db.exec(
        select(PlanningSnapshot).where(
            PlanningSnapshot.org_id == org_id,
            PlanningSnapshot.id == snapshot_id,
        )
    ).one()
    output.write_text(json.dumps(anonymize(snapshot.data), indent=2) + "\n")


def main() -> None:
    from app.core.db import engine

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org-id", type=UUID, required=True)
    parser.add_argument("--snapshot-id", type=UUID, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with Session(engine) as db:
        export_saved(db, args.org_id, args.snapshot_id, args.output)


if __name__ == "__main__":
    main()
