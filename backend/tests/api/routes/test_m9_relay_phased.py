from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.domains.capacity.models import CapacityWeek
from app.domains.org.models import Employee
from app.domains.planning.validator import validate
from app.domains.profile.models import ProfileVersion
from tests.api.routes.test_m7_planning_simulator import data, option, person
from tests.api.routes.test_m7_planning_validator import planning_setup as planning_setup

APPROVAL = "需求方同意分阶段交付"


@pytest.fixture
def relay_case(planning_setup: tuple, db: Session, client: TestClient) -> tuple:
    url, employees, auth, demand = planning_setup
    demand.window_end = demand.window_start + timedelta(days=27)
    replacement = Employee(
        org_id=demand.org_id,
        employee_code="RELAY",
        display_name="合成接力替补",
        department_id=demand.target_department_id,
        work_email="relay@example.com",
    )
    db.add(replacement)
    db.flush()
    db.commit()
    for employee in (employees["E04"], employees["E05"], replacement):
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
                        "statement": "合成接力能力",
                        "level": "routine",
                    }
                ],
            },
        )
        assert response.status_code == 200, response.text
    for code, employee in (("E04", employees["E04"]), ("E05", employees["E05"])):
        for index in range(4):
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
                    confirmed_by_user_id=demand.requester_user_id,
                )
            row.other_percent = 10 if (code == "E04") == (index < 2) else 80
            db.add(row)
            if code == "E04":
                db.add(
                    CapacityWeek(
                        org_id=demand.org_id,
                        employee_id=replacement.id,
                        week_start=week,
                        other_percent=80,
                        confirmed_by_user_id=demand.requester_user_id,
                    )
                )
    db.commit()
    response = client.post(
        f"{url}/planning/demands/{demand.id}/cases", headers=auth["b"]
    )
    assert response.status_code == 200, response.text
    case = response.json()
    assert any(item["kind"] == "phased" for item in case["options"]), case
    phased = option(case["options"], "phased")
    first, second = phased["params"]["assignees"]
    assert first["employee_id"] == str(employees["E04"].id)
    assert second["employee_id"] == str(employees["E05"].id)
    assert first["start_date"] == demand.window_start.isoformat()
    assert second["end_date"] == demand.window_end.isoformat()
    assert first["end_date"] < second["start_date"]
    assert phased["verdict"] == "conditional"
    assert APPROVAL in phased["summary"]["approvers"]
    return f"{url}/planning/cases/{case['id']}/options", auth["b"], phased, replacement


def test_simulated_relay_revisions_keep_approval(
    relay_case: tuple, client: TestClient
) -> None:
    path, auth, current, replacement = relay_case
    employee_id = current["params"]["assignees"][0]["employee_id"]
    for body in (
        {"change": "lock", "employee_ids": [employee_id]},
        {"change": "unlock", "employee_ids": [employee_id]},
        {
            "change": "replace",
            "from_employee_id": employee_id,
            "to_employee_id": str(replacement.id),
        },
        {"change": "allocation", "allocation_percent": 10},
        {
            "change": "dates",
            "start_date": current["params"]["assignees"][0]["start_date"],
            "end_date": current["params"]["assignees"][1]["end_date"],
        },
        {"change": "lock", "employee_ids": [str(replacement.id)]},
    ):
        response = client.post(
            f"{path}/{current['id']}/revise", headers=auth, json=body
        )
        assert response.status_code == 200, response.text
        current = response.json()
        assert current["kind"] == "custom"
        assert current["summary"]["phased_origin"] is True
        assert APPROVAL in current["summary"]["approvers"], body
        assert current["verdict"] != "feasible", body


@pytest.mark.parametrize("kind", ["custom", "direct"])
def test_validator_recognizes_relay_without_kind(kind: str) -> None:
    from app.domains.planning.simulator import simulate

    snapshot = data(weeks=4)
    snapshot["candidates"] = [
        person("first", by_week={0: 80, 1: 80, 2: 20, 3: 20}),
        person("second", by_week={0: 20, 1: 20, 2: 80, 3: 80}),
    ]
    phased = option(simulate(snapshot)[0], "phased")
    assert phased["summary"]["conditions"] == ["分两段交付，需求方要同意"]
    result = validate(snapshot, phased["params"], kind=kind)
    assert APPROVAL in result.summary["approvers"]
    assert result.verdict == "conditional"


def test_parallel_people_do_not_require_phased_approval() -> None:
    from app.domains.planning.simulator import simulate

    snapshot = data(headcount=2)
    snapshot["candidates"] = [person("first"), person("second")]
    direct = option(simulate(snapshot)[0], "direct")
    result = validate(snapshot, direct["params"])
    assert APPROVAL not in result.summary["approvers"]
    assert result.verdict == "feasible"
