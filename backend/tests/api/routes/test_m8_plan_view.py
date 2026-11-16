from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlmodel import Session, select

from app.core.security import create_access_token
from app.domains.capacity.calendar import this_week, week_start
from app.domains.capacity.models import (
    CapacityAssignment,
    CapacityDemand,
    CapacitySetting,
    CapacityWeek,
)
from app.domains.org.models import OrgMembership, RoleGrant
from app.domains.planning.models import (
    OptionKind,
    PlanningCase,
    PlanningOption,
    PlanningSnapshot,
    PlanVerdict,
)
from app.domains.planning.schemas import OptionPublic
from app.domains.planning.simulator import borrow
from app.domains.planning.validator import validate
from app.models import User
from tests.api.routes.test_m7_planning_readonly import business_rows


def case_data(*, in_basis: bool = True) -> tuple[dict, dict]:
    employee_id = str(uuid4())
    data = {
        "demand": {
            "minimum_level": None,
            "headcount": 1,
            "allocation_percent": 50,
            "window_start": "2026-11-16",
            "window_end": "2026-11-29",
            "committed_end_date": None,
        },
        "rules": {"levels": [], "current_week": "2026-11-16"},
        "candidates": [
            {
                "employee_id": employee_id,
                "in_basis": in_basis,
                "newcomer": False,
                "employment_status": "active",
                "level": None,
                "weeks": [
                    {
                        "week_start": week,
                        "capacity_percent": 100,
                        "other_percent": 0,
                        "platform_percent": 0,
                        "status": "confirmed",
                        "confirmed_at": None,
                        "legacy_start": False,
                    }
                    for week in ("2026-11-16", "2026-11-23", "2026-11-30")
                ],
            }
        ],
        "assignments": [
            {
                "assignment_id": str(uuid4()),
                "employee_id": employee_id,
                "start_date": None,
                "counted_from": "2026-11-16",
                "due_date": "2026-11-20",
                "allocation_percent": 20,
            }
        ],
    }
    params = {
        "assignees": [
            {
                "employee_id": employee_id,
                "allocation_percent": 50,
                "start_date": "2026-11-16",
                "end_date": "2026-11-29",
            }
        ]
    }
    return data, params


@pytest.mark.parametrize("kind", list(OptionKind))
@pytest.mark.parametrize("verdict", list(PlanVerdict))
def test_option_labels_cover_all_values(kind: OptionKind, verdict: PlanVerdict) -> None:
    option = OptionPublic(
        id=uuid4(),
        snapshot_id=uuid4(),
        kind=kind,
        params={},
        verdict=verdict,
        findings=[],
        summary={},
        created_at=datetime.now(UTC),
    )
    assert option.model_dump()["kind_label"]
    assert option.model_dump()["verdict_label"]
    assert any("\u4e00" <= character <= "\u9fff" for character in option.kind_label)
    assert any("\u4e00" <= character <= "\u9fff" for character in option.verdict_label)


def test_affected_work_and_approvers_from_snapshot() -> None:
    data, params = case_data(in_basis=False)
    result = validate(data, params)
    assert result.summary["affected_assignment_ids"] == [
        data["assignments"][0]["assignment_id"]
    ]
    assert result.summary["approvers"] == [
        "本部门经理确认答复",
        "经理确认安排依据外人员",
    ]
    data["assignments"][0]["counted_from"] = "2026-12-07"
    assert validate(data, params).summary["affected_assignment_ids"] == []
    del data["assignments"]
    assert validate(data, params).summary["affected_assignment_ids"] == []


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("direct", ["本部门经理确认答复"]),
        ("delay", ["本部门经理确认答复", "需求方接受新日期"]),
        ("reduce_scope", ["本部门经理确认答复", "需求方接受缩小范围"]),
        ("phased", ["本部门经理确认答复", "需求方同意分阶段交付"]),
    ],
)
def test_strategy_approvers(kind: str, expected: list[str]) -> None:
    data, params = case_data()
    summary = validate(data, params, kind=kind).summary
    assert summary["approvers"] == expected
    assert summary["affected_assignment_ids"] == [
        data["assignments"][0]["assignment_id"]
    ]


def test_borrow_approvers_are_roles_only() -> None:
    data, _ = case_data()
    option = borrow(
        data, [{"department_id": str(uuid4()), "department_name": "外部门", "count": 3}]
    )
    assert option["summary"]["approvers"] == [
        "本部门经理确认答复",
        "原部门经理和被借调员工本人同意",
    ]
    assert option["summary"]["affected_assignment_ids"] == []


def test_outside_window_needs_requester_agreement() -> None:
    data, params = case_data()
    params["assignees"][0]["end_date"] = "2026-12-06"
    assert validate(data, params).summary["approvers"] == [
        "本部门经理确认答复",
        "需求方接受新日期",
    ]


@pytest.fixture
def planning_data(demo: tuple, db: Session) -> Generator[tuple]:
    url, employees, auth = demo
    org_id = employees["E01"].org_id
    members = {
        row.employee_id: row
        for row in db.exec(
            select(OrgMembership).where(OrgMembership.org_id == org_id)
        ).all()
    }
    admin = members[None].user_id
    target = employees["E04"].department_id
    assert target is not None
    for code in ("E04", "E07"):
        if employees[code].id not in members:
            user = User(
                username=str(uuid4()),
                email=f"{uuid4()}@example.com",
                hashed_password="unused",
            )
            db.add(user)
            db.flush()
            member = OrgMembership(
                org_id=org_id, employee_id=employees[code].id, user_id=user.id
            )
            db.add(member)
            db.flush()
            members[employees[code].id] = member
    for key, code in (("b", "E05"), ("b_employee", "E04"), ("other_manager", "E07")):
        auth[key] = {
            "Authorization": f"Bearer {create_access_token(members[employees[code].id].user_id, expires_delta=timedelta(minutes=10))}"
        }
    start = this_week(db, org_id)
    demand = CapacityDemand(
        org_id=org_id,
        requesting_department_id=employees["E01"].department_id,
        requester_user_id=members[employees["E01"].id].user_id,
        target_department_id=target,
        capability_key="backend_api",
        capability_name="后端接口",
        headcount=1,
        allocation_percent=50,
        window_start=start,
        window_end=start + timedelta(days=13),
        description="合成需求",
    )
    db.add_all(
        [
            RoleGrant(
                org_id=org_id,
                membership_id=members[employees["E05"].id].id,
                role="dept_manager",
                department_id=target,
            ),
            RoleGrant(
                org_id=org_id,
                membership_id=members[employees["E07"].id].id,
                role="dept_manager",
                department_id=employees["E07"].department_id,
            ),
            CapacitySetting(org_id=org_id, enabled=True, updated_by_user_id=admin),
            demand,
        ]
    )
    db.flush()
    assignment = CapacityAssignment(
        org_id=org_id,
        demand_id=demand.id,
        employee_id=employees["E04"].id,
        allocation_percent=20,
        start_date=None,
        due_date=start + timedelta(days=6),
        assigned_by_user_id=admin,
    )
    db.add(assignment)
    for employee in (employees["E04"], employees["E05"]):
        for week in (start, start + timedelta(days=7)):
            db.add(
                CapacityWeek(
                    org_id=org_id,
                    employee_id=employee.id,
                    week_start=week,
                    other_percent=10,
                    confirmed_by_user_id=admin,
                )
            )
    db.commit()
    yield url, employees, auth, demand, assignment
    db.rollback()
    for model in (
        PlanningOption,
        PlanningSnapshot,
        PlanningCase,
        CapacityAssignment,
        CapacityWeek,
        CapacityDemand,
        CapacitySetting,
        RoleGrant,
    ):
        db.execute(delete(model).where(model.org_id == org_id))
    db.commit()


def test_case_names_and_assignments_respect_scope(
    planning_data: tuple, client: TestClient, db: Session
) -> None:
    url, employees, auth, demand, assignment = planning_data
    before = business_rows(db, demand.org_id)
    response = client.post(
        f"{url}/planning/demands/{demand.id}/cases", headers=auth["b"]
    )
    assert response.status_code == 200, response.text
    case = response.json()
    assert business_rows(db, demand.org_id) == before
    assert case["people"]
    assert {person["employee_id"] for person in case["people"]} == {
        person["employee_id"] for person in case["data"]["candidates"]
    }
    assert employees["E01"].display_name not in {
        p["display_name"] for p in case["people"]
    }
    assert str(employees["E01"].id) not in {p["employee_id"] for p in case["people"]}
    assert not {
        str(person.id)
        for person in employees.values()
        if person.department_id != demand.target_department_id
    } & {person["employee_id"] for person in case["people"]}
    assert all(
        "display_name" not in candidate for candidate in case["data"]["candidates"]
    )
    assert {item["assignment_id"] for item in case["data"]["assignments"]} == {
        str(assignment.id)
    }
    assert case["data"]["assignments"][0]["start_date"] is None
    assert (
        case["data"]["assignments"][0]["counted_from"]
        == week_start(assignment.created_at.date()).isoformat()
    )
    assert any(option["kind"] == "borrow" for option in case["options"])
    case_url = f"{url}/planning/cases/{case['id']}"
    employees["E04"].display_name = "更新后的合成姓名"
    db.commit()
    refreshed = client.get(case_url, headers=auth["b"])
    assert refreshed.status_code == 200
    assert "更新后的合成姓名" in {p["display_name"] for p in refreshed.json()["people"]}
    assert "更新后的合成姓名" not in str(refreshed.json()["data"])
    assert business_rows(db, demand.org_id) == before

    body = {
        "assignees": [
            {
                "employee_id": str(employees["E04"].id),
                "allocation_percent": 50,
                "start_date": demand.window_start.isoformat(),
                "end_date": demand.window_end.isoformat(),
            }
        ]
    }
    for role in ("manager", "employee", "b_employee", "other_manager", "foreign"):
        assert (
            client.post(
                f"{url}/planning/demands/{demand.id}/cases", headers=auth[role]
            ).status_code
            == 404
        )
        assert client.get(case_url, headers=auth[role]).status_code == 404
        assert (
            client.post(
                f"{case_url}/options", headers=auth[role], json=body
            ).status_code
            == 404
        )
