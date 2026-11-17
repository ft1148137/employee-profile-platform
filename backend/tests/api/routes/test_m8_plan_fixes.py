from datetime import timedelta

from fastapi.testclient import TestClient

from tests.api.routes.test_m7_planning_validator import planning_case as planning_case
from tests.api.routes.test_m7_planning_validator import planning_setup as planning_setup
from tests.api.routes.test_m8_plan_revise import source


def test_phased_revisions_keep_approver_until_segments_are_removed(
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
    option = phased.json()
    for body in (
        {"change": "lock", "employee_ids": [str(employees["E04"].id)]},
        {"change": "unlock", "employee_ids": [str(employees["E04"].id)]},
        {"change": "allocation", "allocation_percent": 20},
    ):
        revised = client.post(
            path.replace(base["id"], option["id"]), headers=auth["b"], json=body
        )
        assert revised.status_code == 200, revised.text
        option = revised.json()
        assert "需求方同意分阶段交付" in option["summary"]["approvers"]
    dated = client.post(
        path.replace(base["id"], option["id"]),
        headers=auth["b"],
        json={
            "change": "dates",
            "employee_id": str(employees["E04"].id),
            "start_date": demand.window_start.isoformat(),
            "end_date": demand.window_end.isoformat(),
        },
    )
    assert dated.status_code == 200, dated.text
    assert "需求方同意分阶段交付" not in dated.json()["summary"]["approvers"]


def test_single_employee_dates_name_person_when_merging_phases(
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
    revised = client.post(
        path.replace(base["id"], phased.json()["id"]),
        headers=auth["b"],
        json={
            "change": "dates",
            "employee_id": str(employees["E04"].id),
            "start_date": demand.window_start.isoformat(),
            "end_date": demand.window_end.isoformat(),
        },
    )
    assert revised.status_code == 200, revised.text
    assert revised.json()["summary"]["changes"] == (
        f"把{employees['E04'].display_name}的日期改为"
        f"{demand.window_start.month} 月 {demand.window_start.day} 日至"
        f"{demand.window_end.month} 月 {demand.window_end.day} 日"
    )
