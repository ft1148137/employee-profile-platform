from datetime import date, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.domains.capacity.calendar import this_week
from app.domains.capacity.models import CapacityWeek
from app.domains.org.models import Department
from app.domains.planning import simulator
from tests.api.routes.test_m7_planning_readonly import business_rows
from tests.api.routes.test_m7_planning_validator import planning_setup as planning_setup

START = date(2026, 11, 16)


def data(*, headcount: int = 1, weeks: int = 2) -> dict:
    return {
        "demand": {
            "minimum_level": "routine",
            "headcount": headcount,
            "allocation_percent": 50,
            "window_start": START.isoformat(),
            "window_end": (
                START + timedelta(weeks=weeks) - timedelta(days=1)
            ).isoformat(),
            "committed_start_date": START.isoformat(),
            "committed_end_date": (
                START + timedelta(weeks=weeks) - timedelta(days=1)
            ).isoformat(),
        },
        "rules": {
            "levels": ["beginner", "routine", "expert"],
            "current_week": START.isoformat(),
        },
        "candidates": [],
    }


def person(
    eid: str,
    *,
    newcomer: bool = False,
    level: str = "routine",
    remaining: int = 80,
    by_week: dict[int, int] | None = None,
    confirmed: bool = True,
) -> dict:
    return {
        "employee_id": eid,
        "level": level,
        "in_basis": True,
        "employment_status": "active",
        "newcomer": newcomer,
        "weeks": [
            {
                "week_start": (START + timedelta(weeks=i)).isoformat(),
                "capacity_percent": 100,
                "other_percent": 100 - (by_week or {}).get(i, remaining)
                if confirmed
                else None,
                "platform_percent": 0,
                "status": "confirmed" if confirmed else "unconfirmed",
                "confirmed_at": None,
                "legacy_start": False,
            }
            for i in range(10)
        ],
    }


def option(options: list[dict], kind: str) -> dict:
    return next(o for o in options if o["kind"] == kind)


def test_direct_and_newcomer_sort_by_weekly_remaining() -> None:
    d = data(headcount=2)
    ids = [str(UUID(int=i)) for i in (1, 2, 3, 4)]
    d["candidates"] = [
        person(ids[0], remaining=60),
        person(ids[1], remaining=80),
        person(ids[2], newcomer=True, remaining=70),
        person(ids[3], newcomer=True, remaining=90, level="beginner"),
    ]
    options, notes = simulator.simulate(d)
    assert [
        a["employee_id"] for a in option(options, "direct")["params"]["assignees"]
    ] == [ids[1], ids[2]]
    assert ids[2] in [
        a["employee_id"] for a in option(options, "newcomer")["params"]["assignees"]
    ]
    assert notes == []
    d["candidates"][1]["weeks"][1]["other_percent"] = 60
    assert (
        option(simulator.simulate(d)[0], "direct")["params"]["assignees"][0][
            "employee_id"
        ]
        == ids[2]
    )
    d["candidates"][2]["weeks"][0]["other_percent"] = 60
    options, notes = simulator.simulate(d)
    assert not any(o["kind"] == "newcomer" for o in options)
    assert "没有达到要求且有余量的新人" in notes


def test_missing_capacity_is_kept_for_validator() -> None:
    d = data()
    d["candidates"] = [person(str(uuid4()), confirmed=False)]
    options, _ = simulator.simulate(d)
    assert option(options, "direct")["verdict"] == "insufficient_data"
    assert any(
        f["code"] == "unconfirmed" for f in option(options, "direct")["findings"]
    )


def test_one_snapshot_has_comparable_options() -> None:
    d = data(headcount=2)
    d["candidates"] = [
        person(str(UUID(int=1)), newcomer=True, remaining=80),
        person(str(UUID(int=2)), by_week={0: 80, 1: 20}),
        person(str(UUID(int=3)), by_week={0: 20, 1: 80}),
    ]
    options, _ = simulator.simulate(d)
    options.append(
        simulator.borrow(
            d, [{"department_id": "outside", "department_name": "外部", "count": 2}]
        )
    )
    assert {o["kind"] for o in options} == {
        "direct",
        "newcomer",
        "delay",
        "reduce_scope",
        "phased",
        "borrow",
    }
    assert option(options, "direct")["verdict"] == "infeasible"
    assert option(options, "phased")["verdict"] == "conditional"
    assert len({frozenset(o["summary"]) for o in options}) == 1


def test_delay_rebases_demand_without_mutating_snapshot() -> None:
    d = data()
    d["candidates"] = [person(str(uuid4()), by_week={0: 20, 1: 20})]
    options, notes = simulator.simulate(d)
    delayed = option(options, "delay")
    assert (
        delayed["params"]["assignees"][0]["start_date"]
        == (START + timedelta(weeks=2)).isoformat()
    )
    assert delayed["verdict"] == "conditional"
    assert {f["code"] for f in delayed["findings"]} == {"delay"}
    assert delayed["summary"]["gap_person_weeks"] == 0
    assert d["demand"]["window_start"] == START.isoformat()
    assert "没有达到要求且有余量的新人" in notes


def test_reduce_scope_and_phased() -> None:
    d = data(headcount=2)
    d["candidates"] = [person(str(uuid4()), remaining=60)]
    reduced = option(simulator.simulate(d)[0], "reduce_scope")
    assert reduced["verdict"] == "conditional"
    assert reduced["summary"]["gap_person_weeks"] == 0
    assert any(f["code"] == "reduce_scope" for f in reduced["findings"])
    assert d["demand"]["headcount"] == 2
    phased_data = data()
    phased_data["candidates"] = [
        person(str(UUID(int=1)), by_week={0: 80, 1: 20}),
        person(str(UUID(int=2)), by_week={0: 20, 1: 80}),
    ]
    phased = option(simulator.simulate(phased_data)[0], "phased")
    assert phased["verdict"] == "conditional"
    assert len({a["employee_id"] for a in phased["params"]["assignees"]}) == 2


def test_reduction_reaches_ten_percent_and_ties_are_stable() -> None:
    d = data()
    d["demand"]["allocation_percent"] = 55
    d["candidates"] = [
        person(str(UUID(int=2)), remaining=10),
        person(str(UUID(int=1)), remaining=10),
    ]
    options, _ = simulator.simulate(d)
    assert option(options, "direct")["params"]["assignees"][0]["employee_id"] == str(
        UUID(int=1)
    )
    assert (
        option(options, "reduce_scope")["params"]["assignees"][0]["allocation_percent"]
        == 10
    )


@pytest.mark.parametrize(
    ("headcount", "weeks", "allocation", "gap"),
    [(2, 2, 50, 2.0), (1, 3, 60, 1.8)],
)
def test_borrow_hides_small_counts_and_people(
    headcount: int, weeks: int, allocation: int, gap: float
) -> None:
    snapshot = data(headcount=headcount, weeks=weeks)
    snapshot["demand"]["allocation_percent"] = allocation
    borrowed = simulator.borrow(
        snapshot,
        [
            {"department_id": "outside-1", "department_name": "一部", "count": 2},
            {"department_id": "outside-2", "department_name": "二部", "count": 4},
        ],
    )
    assert borrowed["params"]["departments"] == [
        {
            "department_id": "outside-1",
            "department_name": "一部",
            "count": None,
            "note": "人数较少",
        },
        {"department_id": "outside-2", "department_name": "二部", "count": 4},
    ]
    assert borrowed["verdict"] == "conditional"
    assert borrowed["summary"]["gap_person_weeks"] == gap
    assert "employee_id" not in str(borrowed)


def test_external_department_reference_only_uses_top_level(
    planning_setup: tuple,
    db: Session,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domains.planning import snapshot

    url, _employees, auth, demand = planning_setup
    departments = db.exec(
        select(Department).where(Department.org_id == demand.org_id)
    ).all()
    by_id = {d.id: d for d in departments}
    target = demand.target_department_id
    excluded = {target}
    current = target
    while by_id[current].parent_department_id is not None:
        current = by_id[current].parent_department_id
        excluded.add(current)
    descendants = {target}
    changed = True
    while changed:
        old = len(descendants)
        descendants.update(
            d.id for d in departments if d.parent_department_id in descendants
        )
        changed = len(descendants) != old
    excluded.update(descendants)
    expected = {
        d.id
        for d in departments
        if d.id not in excluded
        and (d.parent_department_id in excluded or d.parent_department_id is None)
    }
    called = []

    def reference(
        _session: Session, _org_id: UUID, dept_id: UUID, _key: str, _minimum: str | None
    ) -> SimpleNamespace:
        called.append(dept_id)
        return SimpleNamespace(department_name=by_id[dept_id].name, count=2)

    monkeypatch.setattr(snapshot, "department_reference", reference)
    result = client.post(f"{url}/planning/demands/{demand.id}/cases", headers=auth["b"])
    assert result.status_code == 200, result.text
    assert set(called) == expected
    if expected:
        borrowed = option(result.json()["options"], "borrow")
        assert all(
            d["count"] is None and d["note"] == "人数较少"
            for d in borrowed["params"]["departments"]
        )
        assert not any(
            k in str(borrowed)
            for k in ["employee_id", "display_name", "employee_code", "band"]
        )


def test_case_creates_options_without_capacity_writes(
    planning_setup: tuple, db: Session, client: TestClient
) -> None:
    url, employees, auth, demand = planning_setup
    org_id = demand.org_id
    start = this_week(db, org_id)
    for week in range(2, 10):
        for employee in (employees["E04"], employees["E05"]):
            db.add(
                CapacityWeek(
                    org_id=org_id,
                    employee_id=employee.id,
                    week_start=start + timedelta(weeks=week),
                    other_percent=10,
                    confirmed_by_user_id=demand.requester_user_id,
                )
            )
    db.commit()
    before = business_rows(db, org_id)
    response = client.post(
        f"{url}/planning/demands/{demand.id}/cases", headers=auth["b"]
    )
    assert response.status_code == 200, response.text
    case = response.json()
    assert business_rows(db, org_id) == before
    assert "notes" in case
    assert case["options"][0]["kind"] == "direct"
    assert all(
        set(o["summary"])
        == {
            "finish_date",
            "covered_headcount",
            "gap_person_weeks",
            "conditions",
            "newcomer_count",
            "affected_assignment_ids",
            "approvers",
        }
        for o in case["options"]
    )
    assert (
        client.get(
            f"{url}/planning/cases/{case['id']}", headers=auth["manager"]
        ).status_code
        == 404
    )
