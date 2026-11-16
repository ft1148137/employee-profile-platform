import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.domains.capacity.models import CapacitySetting
from app.domains.org.models import Department, OrgMembership, RoleGrant
from app.domains.planning.models import PlanningOption, PlanningSnapshot
from app.models import User
from tests.api.routes.test_m7_planning_validator import planning_case as planning_case
from tests.api.routes.test_m7_planning_validator import planning_setup as planning_setup


@pytest.mark.parametrize("change", ["inactive", "outside", "sibling"])
def test_old_case_checks_current_employee_scope(
    planning_case: tuple, client: TestClient, db: Session, change: str
) -> None:
    url, employees, auth, demand, case = planning_case
    person = employees["E04"]
    assert str(person.id) in {
        candidate["employee_id"] for candidate in case["data"]["candidates"]
    }
    case_url = f"{url}/planning/cases/{case['id']}"
    old_snapshot = db.get(PlanningSnapshot, case["snapshot_id"])
    assert old_snapshot is not None
    old_data = json.dumps(old_snapshot.data, sort_keys=True)
    old_options = db.exec(
        select(PlanningOption).where(
            PlanningOption.org_id == demand.org_id,
            PlanningOption.snapshot_id == old_snapshot.id,
        )
    ).all()
    assert old_options
    old_option_data = [option.model_dump(mode="json") for option in old_options]

    if change == "inactive":
        person.employment_status = "inactive"
    elif change == "outside":
        person.department_id = employees["E07"].department_id
    else:
        parent = Department(org_id=demand.org_id, name="合成上级部门")
        sibling = Department(org_id=demand.org_id, name="合成同级部门")
        db.add_all([parent, sibling])
        db.flush()
        target = db.get(Department, demand.target_department_id)
        assert target is not None
        target.parent_department_id = parent.id
        sibling.parent_department_id = parent.id
        person.department_id = sibling.id
        b_member = db.exec(
            select(OrgMembership).where(
                OrgMembership.org_id == demand.org_id,
                OrgMembership.employee_id == employees["E05"].id,
            )
        ).one()
        db.add(
            RoleGrant(
                org_id=demand.org_id,
                membership_id=b_member.id,
                role="dept_manager",
                department_id=parent.id,
            )
        )

    db.commit()
    admin_view = client.get(case_url, headers=auth["admin"])
    assert admin_view.status_code == 200
    assert admin_view.json()["stale"] is True

    admin_member = db.exec(
        select(OrgMembership)
        .join(User, OrgMembership.user_id == User.id)
        .where(
            OrgMembership.org_id == demand.org_id,
            User.email == "admin@demo.example.com",
        )
    ).one()
    db.add(
        RoleGrant(
            org_id=demand.org_id,
            membership_id=admin_member.id,
            role="dept_manager",
            department_id=demand.target_department_id,
        )
    )
    db.commit()

    admin_view = client.get(case_url, headers=auth["admin"])
    assert admin_view.status_code == 200
    assert admin_view.json()["stale"] is True
    assert str(person.id) in {
        candidate["employee_id"]
        for candidate in admin_view.json()["data"]["candidates"]
    }

    current = client.get(case_url, headers=auth["b"])
    if change == "sibling":
        assert current.status_code == 200
        assert current.json()["stale"] is True
    else:
        assert current.status_code == 409
        assert current.json() == {
            "detail": "部分候选人已不在当前管辖范围内，请重新模拟"
        }
        for secret in (
            str(person.id),
            person.display_name,
            "routine",
            "weeks",
            "other_percent",
            "level",
        ):
            assert secret not in current.text
        for role in ("manager", "employee", "foreign"):
            assert client.get(case_url, headers=auth[role]).status_code == 404
        setting = db.exec(
            select(CapacitySetting).where(CapacitySetting.org_id == demand.org_id)
        ).one()
        setting.enabled = False
        db.commit()
        assert client.get(case_url, headers=auth["b"]).status_code == 404
        setting.enabled = True
        db.commit()
        if change == "outside":
            successor = db.exec(
                select(OrgMembership).where(
                    OrgMembership.org_id == demand.org_id,
                    OrgMembership.employee_id == employees["E01"].id,
                )
            ).one()
            db.add(
                RoleGrant(
                    org_id=demand.org_id,
                    membership_id=successor.id,
                    role="dept_manager",
                    department_id=demand.target_department_id,
                )
            )
            db.commit()
            assert client.get(case_url, headers=auth["manager"]).status_code == 409

    refreshed = client.post(
        f"{url}/planning/demands/{demand.id}/cases", headers=auth["b"]
    )
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["snapshot_id"] != case["snapshot_id"]
    assert str(person.id) not in {
        candidate["employee_id"] for candidate in refreshed.json()["data"]["candidates"]
    }
    after = client.get(case_url, headers=auth["b"])
    assert after.status_code == 200
    assert after.json()["snapshot_id"] == refreshed.json()["snapshot_id"]
    assert after.json()["stale"] is False
    db.expire_all()
    old_snapshot_after = db.get(PlanningSnapshot, old_snapshot.id)
    assert old_snapshot_after is not None
    assert json.dumps(old_snapshot_after.data, sort_keys=True) == old_data
    assert [
        option.model_dump(mode="json")
        for option in db.exec(
            select(PlanningOption).where(
                PlanningOption.org_id == demand.org_id,
                PlanningOption.snapshot_id == old_snapshot.id,
            )
        ).all()
    ] == old_option_data


def test_case_with_external_custom_assignee_stays_readable(
    planning_case: tuple, client: TestClient
) -> None:
    url, employees, auth, demand, case = planning_case
    case_url = f"{url}/planning/cases/{case['id']}"
    outsider_id = str(employees["E07"].id)
    assert outsider_id not in {
        candidate["employee_id"] for candidate in case["data"]["candidates"]
    }
    response = client.post(
        f"{case_url}/options",
        headers=auth["b"],
        json={
            "assignees": [
                {
                    "employee_id": outsider_id,
                    "allocation_percent": 50,
                    "start_date": demand.window_start.isoformat(),
                    "end_date": demand.window_end.isoformat(),
                }
            ]
        },
    )
    assert response.status_code == 200, response.text
    current = client.get(case_url, headers=auth["b"])
    assert current.status_code == 200
    assert current.json()["stale"] is False
    assert any(option["kind"] == "custom" for option in current.json()["options"])
