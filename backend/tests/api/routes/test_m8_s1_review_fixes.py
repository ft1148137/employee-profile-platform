from datetime import timedelta

from fastapi.testclient import TestClient

from app.domains.planning.validator import validate
from tests.api.routes.test_m7_planning_validator import params, snapshot
from tests.api.routes.test_m7_planning_validator import planning_case as planning_case
from tests.api.routes.test_m7_planning_validator import planning_setup as planning_setup
from tests.api.routes.test_m8_plan_revise import source


def test_reduced_custom_allocation_needs_requester_approval() -> None:
    result = validate(snapshot(), params(percent=20), kind="custom")
    assert "reduced_allocation" in {item["code"] for item in result.findings}
    assert "需求方接受缩小范围" in result.summary["approvers"]


def test_revised_allocation_needs_requester_approval(
    planning_case: tuple, client: TestClient
) -> None:
    path, _, auth, _, _, _ = source(planning_case, client)
    response = client.post(
        path, headers=auth["b"], json={"change": "allocation", "allocation_percent": 20}
    )
    assert response.status_code == 200, response.text
    option = response.json()
    assert "reduced_allocation" in {item["code"] for item in option["findings"]}
    assert "需求方接受缩小范围" in option["summary"]["approvers"]


def test_revised_phased_dates_merge_same_person(
    planning_case: tuple, client: TestClient
) -> None:
    path, employees, auth, demand, _, base = source(planning_case, client)
    phased = client.post(
        path,
        headers=auth["b"],
        json={
            "change": "phased",
            "split_date": (demand.window_start + timedelta(days=7)).isoformat(),
        },
    )
    assert phased.status_code == 200, phased.text
    assert len(phased.json()["params"]["assignees"]) == 2
    revised = client.post(
        path.replace(base["id"], phased.json()["id"]),
        headers=auth["b"],
        json={
            "change": "dates",
            "start_date": demand.window_start.isoformat(),
            "end_date": demand.window_end.isoformat(),
        },
    )
    assert revised.status_code == 200, revised.text
    option = revised.json()
    assert option["params"]["assignees"] == [
        {
            "employee_id": str(employees["E04"].id),
            "allocation_percent": 50,
            "start_date": demand.window_start.isoformat(),
            "end_date": demand.window_end.isoformat(),
        }
    ]
    assert "over_capacity" not in {item["code"] for item in option["findings"]}


def test_revised_dates_reject_unequal_phases(
    planning_case: tuple, client: TestClient
) -> None:
    url, employees, auth, demand, case = planning_case
    split = demand.window_start + timedelta(days=7)
    created = client.post(
        f"{url}/planning/cases/{case['id']}/options",
        headers=auth["b"],
        json={
            "assignees": [
                {
                    "employee_id": str(employees["E04"].id),
                    "allocation_percent": percent,
                    "start_date": start.isoformat(),
                    "end_date": end.isoformat(),
                }
                for percent, start, end in (
                    (40, demand.window_start, split - timedelta(days=1)),
                    (50, split, demand.window_end),
                )
            ]
        },
    )
    assert created.status_code == 200, created.text
    revised = client.post(
        f"{url}/planning/cases/{case['id']}/options/{created.json()['id']}/revise",
        headers=auth["b"],
        json={
            "change": "dates",
            "start_date": demand.window_start.isoformat(),
            "end_date": demand.window_end.isoformat(),
        },
    )
    assert revised.status_code == 400
    assert revised.json()["detail"] == (
        "分阶段方案中同一人多段投入不同，请按原方案重新修改日期"
    )
