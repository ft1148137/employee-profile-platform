from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.security import create_access_token
from app.domains.org.models import OrgMembership, RoleGrant
from app.domains.planning.models import OptionKind, PlanningOption, PlanVerdict
from app.models import User
from tests.api.routes.test_m7_planning_readonly import business_rows
from tests.api.routes.test_m7_planning_validator import planning_case as planning_case
from tests.api.routes.test_m7_planning_validator import planning_setup as planning_setup


def source(
    planning_case: tuple, client: TestClient
) -> tuple[str, dict, dict, object, dict, dict]:
    url, employees, auth, demand, case = planning_case
    created = client.post(
        f"{url}/planning/cases/{case['id']}/options",
        headers=auth["b"],
        json={
            "assignees": [
                {
                    "employee_id": str(employees["E04"].id),
                    "allocation_percent": 50,
                    "start_date": demand.window_start.isoformat(),
                    "end_date": demand.window_end.isoformat(),
                }
            ]
        },
    )
    assert created.status_code == 200, created.text
    option = created.json()
    path = f"{url}/planning/cases/{case['id']}/options/{option['id']}/revise"
    return path, employees, auth, demand, case, option


@pytest.mark.parametrize(
    ("body", "text"),
    [
        (
            {"change": "replace", "from_employee_id": "E04", "to_employee_id": "E05"},
            "换成",
        ),
        ({"change": "dates", "start_date": "start", "end_date": "later"}, "日期"),
        ({"change": "allocation", "allocation_percent": 20}, "投入"),
        ({"change": "phased", "split_date": "middle"}, "分阶段"),
        ({"change": "lock", "employee_ids": ["E04"]}, "锁定"),
        ({"change": "unlock", "employee_ids": ["E04"]}, "解锁"),
    ],
)
def test_revise_each_change(
    planning_case: tuple,
    client: TestClient,
    db: Session,
    body: dict,
    text: str,
) -> None:
    path, employees, auth, demand, case, base = source(planning_case, client)
    before = business_rows(db, demand.org_id)
    body = body.copy()
    for field in ("from_employee_id", "to_employee_id"):
        if field in body:
            body[field] = str(employees[body[field]].id)
    if "employee_ids" in body:
        body["employee_ids"] = [
            str(employees[code].id) for code in body["employee_ids"]
        ]
    if body.get("start_date") == "start":
        body["start_date"] = demand.window_start.isoformat()
        body["end_date"] = (demand.window_end + timedelta(days=7)).isoformat()
    if body.get("split_date") == "middle":
        body["split_date"] = (demand.window_start + timedelta(days=7)).isoformat()
    if body["change"] == "unlock":
        locked = client.post(
            path,
            headers=auth["b"],
            json={"change": "lock", "employee_ids": body["employee_ids"]},
        )
        assert locked.status_code == 200, locked.text
        path = path.replace(base["id"], locked.json()["id"])
        base = locked.json()
    response = client.post(path, headers=auth["b"], json=body)
    assert response.status_code == 200, response.text
    option = response.json()
    assert option["kind"] == "custom"
    assert option["snapshot_id"] == case["snapshot_id"]
    assert option["summary"]["base_option_id"] == base["id"]
    assert text in option["summary"]["changes"]
    assert set(option["summary"]["diff"]) >= {
        "verdict",
        "finish_date",
        "covered_headcount",
        "gap_person_weeks",
        "added_findings",
        "resolved_findings",
    }
    for field in ("verdict", "finish_date", "covered_headcount", "gap_person_weeks"):
        assert option["summary"]["diff"][field]["before"] == (
            base[field] if field == "verdict" else base["summary"][field]
        )
        assert option["summary"]["diff"][field]["after"] == (
            option[field] if field == "verdict" else option["summary"][field]
        )
    assert option["params"]["assignees"]
    if body["change"] == "replace":
        assert option["params"]["assignees"][0]["employee_id"] == str(
            employees["E05"].id
        )
    elif body["change"] == "dates":
        assert option["params"]["assignees"][0]["end_date"] == body["end_date"]
    elif body["change"] == "allocation":
        assert option["params"]["assignees"][0]["allocation_percent"] == 20
    elif body["change"] == "phased":
        assert len(option["params"]["assignees"]) == 2
    elif body["change"] == "lock":
        assert option["params"]["locked_employee_ids"] == body["employee_ids"]
    else:
        assert option["params"]["locked_employee_ids"] == []
    assert business_rows(db, demand.org_id) == before


def test_lock_blocks_edits_until_unlock(
    planning_case: tuple, client: TestClient, db: Session
) -> None:
    path, employees, auth, demand, _, base = source(planning_case, client)
    person = str(employees["E04"].id)
    before = business_rows(db, demand.org_id)
    locked = client.post(
        path, headers=auth["b"], json={"change": "lock", "employee_ids": [person]}
    )
    assert locked.status_code == 200, locked.text
    path = path.replace(base["id"], locked.json()["id"])
    for change in (
        {
            "change": "replace",
            "from_employee_id": person,
            "to_employee_id": str(employees["E05"].id),
        },
        {
            "change": "dates",
            "start_date": demand.window_start.isoformat(),
            "end_date": demand.window_end.isoformat(),
        },
        {"change": "allocation", "allocation_percent": 20},
    ):
        rejected = client.post(path, headers=auth["b"], json=change)
        assert rejected.status_code == 409
        assert rejected.json()["detail"] == "该人员已锁定，请先解锁"
    unlocked = client.post(
        path, headers=auth["b"], json={"change": "unlock", "employee_ids": [person]}
    )
    assert unlocked.status_code == 200, unlocked.text
    path = path.replace(locked.json()["id"], unlocked.json()["id"])
    allowed = client.post(
        path, headers=auth["b"], json={"change": "allocation", "allocation_percent": 20}
    )
    assert allowed.status_code == 200, allowed.text
    assert business_rows(db, demand.org_id) == before


def test_revise_rejects_invalid_source_candidate_and_stale_snapshot(
    planning_case: tuple, client: TestClient, db: Session
) -> None:
    path, employees, auth, demand, case, base = source(planning_case, client)
    before = business_rows(db, demand.org_id)
    outsider = client.post(
        path,
        headers=auth["b"],
        json={
            "change": "replace",
            "from_employee_id": base["params"]["assignees"][0]["employee_id"],
            "to_employee_id": str(employees["E01"].id),
        },
    )
    assert outsider.status_code == 400
    borrow = PlanningOption(
        org_id=demand.org_id,
        snapshot_id=case["snapshot_id"],
        kind=OptionKind.BORROW,
        params={"departments": []},
        verdict=PlanVerdict.CONDITIONAL,
        findings=[],
        summary={},
    )
    db.add(borrow)
    db.commit()
    rejected = client.post(
        path.replace(base["id"], str(borrow.id)),
        headers=auth["b"],
        json={"change": "allocation", "allocation_percent": 20},
    )
    assert rejected.status_code == 400
    assert rejected.json()["detail"] == "借调方案不能快捷修改，请在其他方案上修改"
    demand.revision += 1
    db.commit()
    stale = client.post(
        path, headers=auth["b"], json={"change": "allocation", "allocation_percent": 20}
    )
    assert stale.status_code == 409
    assert stale.json()["detail"] == "数据已变化，请重新模拟"
    assert (
        business_rows(db, demand.org_id) != before
    )  # Only the test's revision changed.
    refreshed = client.post(
        f"{planning_case[0]}/planning/demands/{demand.id}/cases", headers=auth["b"]
    )
    assert refreshed.status_code == 200, refreshed.text
    old = client.post(
        path, headers=auth["b"], json={"change": "allocation", "allocation_percent": 20}
    )
    assert old.status_code == 409


def test_revise_requires_same_case_access(
    planning_case: tuple, client: TestClient, db: Session
) -> None:
    path, employees, auth, demand, _, _ = source(planning_case, client)
    before = business_rows(db, demand.org_id)
    body = {"change": "allocation", "allocation_percent": 20}
    for role in ("manager", "employee", "foreign"):
        assert client.post(path, headers=auth[role], json=body).status_code == 404
    for code, manager in (("E04", False), ("E07", True)):
        user = User(
            username=str(uuid4()),
            email=f"{uuid4()}@example.com",
            hashed_password="unused",
        )
        db.add(user)
        db.flush()
        member = OrgMembership(
            org_id=demand.org_id, user_id=user.id, employee_id=employees[code].id
        )
        db.add(member)
        db.flush()
        if manager:
            db.add(
                RoleGrant(
                    org_id=demand.org_id,
                    membership_id=member.id,
                    role="dept_manager",
                    department_id=employees[code].department_id,
                )
            )
        db.commit()
        header = {
            "Authorization": f"Bearer {create_access_token(user.id, expires_delta=timedelta(minutes=10))}"
        }
        assert client.post(path, headers=header, json=body).status_code == 404
    assert business_rows(db, demand.org_id) == before


def test_create_option_accepts_locks(planning_case: tuple, client: TestClient) -> None:
    _, employees, auth, _, case, original = source(planning_case, client)
    url = planning_case[0]
    assignee = original["params"]["assignees"][0]
    body = {"assignees": [assignee], "locked_employee_ids": [assignee["employee_id"]]}
    response = client.post(
        f"{url}/planning/cases/{case['id']}/options", headers=auth["b"], json=body
    )
    assert response.status_code == 200, response.text
    assert (
        response.json()["params"]["locked_employee_ids"] == body["locked_employee_ids"]
    )
    invalid = client.post(
        f"{url}/planning/cases/{case['id']}/options",
        headers=auth["b"],
        json={**body, "locked_employee_ids": [str(employees["E01"].id)]},
    )
    assert invalid.status_code == 422
