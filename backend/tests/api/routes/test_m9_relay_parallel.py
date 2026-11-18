from copy import deepcopy
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.domains.capacity.models import CapacityWeek
from app.domains.org.models import Employee
from app.domains.planning.validator import validate
from app.domains.profile.models import ProfileVersion
from tests.api.routes.test_m7_planning_simulator import START, data, person
from tests.api.routes.test_m7_planning_validator import planning_case as planning_case
from tests.api.routes.test_m7_planning_validator import planning_setup as planning_setup

APPROVAL = "需求方同意分阶段交付"


@pytest.fixture
def parallel_setup(planning_setup: tuple, db: Session, client: TestClient) -> tuple:
    url, employees, auth, demand = planning_setup
    people = [employees["E04"], employees["E05"]]
    for index in range(4):
        employee = Employee(
            org_id=demand.org_id,
            employee_code=f"PARALLEL-{index}",
            display_name=f"合成接力员工{index}",
            department_id=demand.target_department_id,
            work_email=f"parallel-{index}@example.com",
        )
        db.add(employee)
        people.append(employee)
    db.commit()
    for employee in people:
        previous = db.exec(
            select(ProfileVersion)
            .where(
                ProfileVersion.org_id == demand.org_id,
                ProfileVersion.subject_employee_id == employee.id,
            )
            .order_by(ProfileVersion.revision.desc())
        ).first()
        response = client.post(
            f"{url}/employees/{employee.id}/profile/direct-edits",
            headers=auth["admin"],
            json={
                "base_revision": previous.revision if previous else None,
                "items": [
                    {
                        "op": "add",
                        "capability_key": demand.capability_key,
                        "statement": "合成并行接力能力",
                        "level": "routine",
                    }
                ],
            },
        )
        assert response.status_code == 200, response.text
        for index in range(6):
            week = demand.window_start + timedelta(weeks=index)
            row = db.exec(
                select(CapacityWeek).where(
                    CapacityWeek.org_id == demand.org_id,
                    CapacityWeek.employee_id == employee.id,
                    CapacityWeek.week_start == week,
                )
            ).one_or_none()
            if row is None:
                row = CapacityWeek(
                    org_id=demand.org_id,
                    employee_id=employee.id,
                    week_start=week,
                    other_percent=10,
                    confirmed_by_user_id=demand.requester_user_id,
                )
                db.add(row)
    db.commit()
    return url, people, auth, demand


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize(
    ("weeks", "headcount", "periods"),
    [
        (4, 2, [(0, 4), (0, 2), (2, 4)]),
        (6, 2, [(0, 6), (0, 2), (2, 4), (4, 6)]),
        (6, 3, [(0, 6), (0, 3), (3, 6), (0, 2), (2, 4), (4, 6)]),
        (4, 2, [(0, 4), (0, 4)]),
    ],
    ids=["full-and-relay", "three-phases", "two-relay-slots", "full-parallel"],
)
def test_rest_parallel_relay(
    parallel_setup: tuple,
    db: Session,
    client: TestClient,
    weeks: int,
    headcount: int,
    periods: list[tuple[int, int]],
    reverse: bool,
) -> None:
    url, people, auth, demand = parallel_setup
    demand.window_end = demand.window_start + timedelta(weeks=weeks, days=-1)
    demand.headcount = headcount
    db.commit()
    response = client.post(
        f"{url}/planning/demands/{demand.id}/cases", headers=auth["b"]
    )
    assert response.status_code == 200, response.text
    case = response.json()
    body = {
        "assignees": [
            {
                "employee_id": str(employee.id),
                "allocation_percent": 50 if weeks == 4 else 50 + index,
                "start_date": (
                    demand.window_start + timedelta(weeks=start)
                ).isoformat(),
                "end_date": (
                    demand.window_start + timedelta(weeks=end, days=-1)
                ).isoformat(),
            }
            for index, (employee, (start, end)) in enumerate(
                zip(people, periods, strict=False)
            )
        ],
        "locked_employee_ids": [],
    }
    if reverse:
        body["assignees"].reverse()
    response = client.post(
        f"{url}/planning/cases/{case['id']}/options", headers=auth["b"], json=body
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["kind"] == "custom"
    assert result["params"] == body
    assert result["summary"]["covered_headcount"] == headcount
    if len(periods) > headcount:
        assert result["verdict"] == "conditional"
        assert result["summary"]["conditions"] == [APPROVAL]
        assert APPROVAL in result["summary"]["approvers"]
        assert {finding["code"] for finding in result["findings"]} == {"phased"}
    else:
        assert result["verdict"] == "feasible"
        assert result["summary"]["conditions"] == []
        assert APPROVAL not in result["summary"]["approvers"]


def test_rest_short_assignment_starting_at_minimum_date(
    planning_case: tuple, client: TestClient
) -> None:
    url, employees, auth, _, case = planning_case
    assignees = [
        {
            "employee_id": str(employees["E04"].id),
            "allocation_percent": 50,
            "start_date": date.min.isoformat(),
            "end_date": (date.min + timedelta(days=1)).isoformat(),
        }
    ]
    response = client.post(
        f"{url}/planning/cases/{case['id']}/options",
        headers=auth["b"],
        json={"assignees": assignees},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["params"]["assignees"] == assignees
    assert result["verdict"] == "infeasible"
    assert result["summary"]["finish_date"] == assignees[0]["end_date"]
    assert result["summary"]["covered_headcount"] == 0
    codes = {finding["code"] for finding in result["findings"]}
    assert {"outside_window", "headcount_gap"} <= codes
    assert "phased" not in codes
    assert APPROVAL not in result["summary"]["approvers"]


@pytest.mark.parametrize(
    ("problem", "verdict", "code"),
    [
        ("over", "infeasible", "over_capacity"),
        ("unknown", "insufficient_data", "unconfirmed"),
        ("gap", "infeasible", "headcount_gap"),
    ],
)
def test_relay_keeps_more_severe_verdicts(
    problem: str, verdict: str, code: str
) -> None:
    snapshot = data(headcount=2, weeks=4)
    snapshot["candidates"] = [person(str(index)) for index in range(3)]
    plan = {
        "assignees": [
            {
                "employee_id": str(index),
                "allocation_percent": 50,
                "start_date": (START + timedelta(weeks=start)).isoformat(),
                "end_date": (START + timedelta(weeks=end, days=-1)).isoformat(),
            }
            for index, (start, end) in enumerate([(0, 4), (0, 2), (2, 4)])
        ]
    }
    if problem == "over":
        snapshot["candidates"][1]["weeks"][0]["other_percent"] = 80
    elif problem == "unknown":
        snapshot["candidates"][1]["weeks"][0]["status"] = "unconfirmed"
        snapshot["candidates"][1]["weeks"][0]["other_percent"] = None
    else:
        snapshot["demand"]["headcount"] = 3
    before = deepcopy(plan)
    result = validate(snapshot, plan)
    assert result.verdict == verdict
    assert {"phased", code} <= {finding["code"] for finding in result.findings}
    assert APPROVAL in result.summary["conditions"]
    assert APPROVAL in result.summary["approvers"]
    assert plan == before


def test_disconnected_partial_periods_do_not_form_a_relay() -> None:
    snapshot = data(weeks=4)
    snapshot["candidates"] = [person(str(index)) for index in range(3)]
    plan = {
        "assignees": [
            {
                "employee_id": str(index),
                "allocation_percent": 50,
                "start_date": (START + timedelta(weeks=start)).isoformat(),
                "end_date": (START + timedelta(weeks=end, days=-1)).isoformat(),
            }
            for index, (start, end) in enumerate([(0, 4), (0, 1), (2, 4)])
        ]
    }
    result = validate(snapshot, plan)
    assert result.verdict == "feasible"
    assert APPROVAL not in result.summary["approvers"]
