"""Real API regression cases for the deterministic Planning baseline."""

import json
import os
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlmodel import Session, select

from app.core.security import create_access_token
from app.domains.base import utc_now
from app.domains.capability.models import (
    CapabilityDefinition,
    CapabilitySchemaVersion,
    SchemaStatus,
)
from app.domains.capacity.calendar import this_week
from app.domains.capacity.models import (
    CapacityAssignment,
    CapacityDemand,
    CapacityDemandEvent,
    CapacityExtensionRequest,
    CapacitySetting,
    CapacityUnavailability,
    CapacityWeek,
    CapacityWorkloadRequest,
)
from app.domains.org.models import (
    Department,
    Employee,
    Organization,
    OrgMembership,
    RoleGrant,
)
from app.domains.planning.models import PlanningCase, PlanningOption, PlanningSnapshot
from app.domains.profile.models import ProfileVersion
from app.models import User
from tests.api.routes.test_m7_planning_readonly import business_rows
from tests.utils.org import cleanup_new_users, delete_test_organization

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
CASES = sorted((HERE / "cases").glob("*.yaml"))
SELECTED = os.environ.get("PLANNING_EVAL_CASE")
if SELECTED:
    CASES = [path for path in CASES if path.stem == SELECTED]


def test_case_coverage() -> None:
    if SELECTED:
        return
    counts = Counter(yaml.safe_load(path.read_text())["category"] for path in CASES)
    assert len(CASES) >= 30
    assert len(counts) == 16
    assert min(counts.values()) >= 2


def test_setup_failure_cleans_organization(db: Session, client: TestClient) -> None:
    before = set(db.exec(select(Organization.id)).all())
    with pytest.raises(FileNotFoundError):
        _exercise(db, client, {"enterprise": "missing"})
    assert set(db.exec(select(Organization.id)).all()) == before


def _cleanup(db: Session, org_id: UUID, existing_users: set[UUID]) -> None:
    db.rollback()
    if db.get(Organization, org_id) is not None:
        for model in (
            PlanningOption,
            PlanningSnapshot,
            PlanningCase,
            CapacityExtensionRequest,
            CapacityAssignment,
            CapacityDemandEvent,
            CapacityDemand,
            CapacityWorkloadRequest,
            CapacityUnavailability,
            CapacityWeek,
            CapacitySetting,
        ):
            db.execute(delete(model).where(model.org_id == org_id))
        db.commit()
        delete_test_organization(db, org_id)
    cleanup_new_users(db, existing_users)
    db.commit()


def _setup(
    db: Session, case: dict, org: Organization
) -> tuple[UUID, dict, dict, CapacityDemand, date]:
    enterprise = yaml.safe_load(
        (
            ROOT / "examples/test-enterprises" / case["enterprise"] / "org.yaml"
        ).read_text()
    )
    roster = {person["employee_code"]: person for person in enterprise["employees"]}
    a_code, b_code, a_employee_code = case["roles"]
    a_dept = roster[a_code]["department"]
    b_dept = roster[b_code]["department"]
    org.name = enterprise["name"]
    db.add(org)
    dept_names = {
        a_dept,
        b_dept,
        *(roster[p["code"]]["department"] for p in case["people"]),
    }
    departments = {name: Department(org_id=org.id, name=name) for name in dept_names}
    db.add_all(departments.values())
    db.flush()
    users = {}
    employees = {}
    for code in {a_code, b_code, a_employee_code, *(p["code"] for p in case["people"])}:
        row = roster[code]
        employee = Employee(
            org_id=org.id,
            department_id=departments[row["department"]].id,
            employee_code=code,
            display_name=row["display_name"],
            work_email=row["work_email"],
            employment_status="inactive"
            if any(p["code"] == code and p.get("inactive") for p in case["people"])
            else "active",
            hire_date=date.today()
            - timedelta(
                days=next(
                    (
                        p.get("hire_days_ago", 730)
                        for p in case["people"]
                        if p["code"] == code
                    ),
                    730,
                )
            ),
        )
        employees[code] = employee
        db.add(employee)
    db.flush()
    for code in (a_code, b_code, a_employee_code):
        user = User(
            username=f"planning-{uuid4().hex}",
            email=f"{uuid4().hex}@example.com",
            hashed_password="unused",
        )
        db.add(user)
        db.flush()
        member = OrgMembership(
            org_id=org.id, user_id=user.id, employee_id=employees[code].id
        )
        db.add(member)
        db.flush()
        users[code] = user
        if code != a_employee_code:
            db.add(
                RoleGrant(
                    org_id=org.id,
                    membership_id=member.id,
                    role="dept_manager",
                    department_id=employees[code].department_id,
                )
            )
    schema = CapabilitySchemaVersion(
        org_id=org.id, version=1, status=SchemaStatus.ACTIVE
    )
    db.add(schema)
    db.flush()
    capability = CapabilityDefinition(
        org_id=org.id,
        schema_version_id=schema.id,
        key="planning-eval",
        name="模拟能力",
        level_rubric={
            "levels": [
                {"key": key, "label": key}
                for key in ("needs_support", "routine", "independent_complex")
            ]
        },
    )
    db.add(capability)
    db.flush()
    start = this_week(db, org.id)
    for person in case["people"]:
        employee = employees[person["code"]]
        if person.get("level"):
            db.add(
                ProfileVersion(
                    org_id=org.id,
                    subject_employee_id=employee.id,
                    revision=1,
                    schema_version_id=schema.id,
                    published_at=utc_now(),
                    published_by={"actor_type": "system_worker"},
                    snapshot={
                        "claims": [
                            {
                                "id": str(uuid4()),
                                "capability_definition_id": str(capability.id),
                                "statement": "合成能力判断",
                                "level": person["level"],
                                "assessment_status": "confirmed",
                                "confidence": None,
                                "explanation": None,
                                "generated_by": {"actor_type": "user"},
                                "evidence": [],
                            }
                        ]
                    },
                )
            )
        weekly = person.get("weekly_other")
        for index in range(10):
            if index in person.get("missing_weeks", []):
                continue
            other = weekly[index] if weekly is not None else person.get("other", 10)
            db.add(
                CapacityWeek(
                    org_id=org.id,
                    employee_id=employee.id,
                    week_start=start + timedelta(weeks=index),
                    other_percent=other,
                    confirmed_by_user_id=users[b_code].id,
                    confirmed_at=utc_now()
                    - timedelta(days=person.get("confirmed_days_ago", 0)),
                )
            )
    for entry in case.get("unavailability", []):
        db.add(
            CapacityUnavailability(
                org_id=org.id,
                employee_id=employees[entry["code"]].id,
                start_date=start + timedelta(days=entry["start_day"]),
                end_date=start + timedelta(days=entry["end_day"]),
                created_by_user_id=users[b_code].id,
            )
        )
    demand_config = case.get("demand", {})
    demand = CapacityDemand(
        org_id=org.id,
        requesting_department_id=departments[a_dept].id,
        requester_user_id=users[a_code].id,
        target_department_id=departments[b_dept].id,
        capability_key=capability.key,
        capability_name=capability.name,
        minimum_level=demand_config.get("minimum_level", "routine"),
        headcount=demand_config.get("headcount", 1),
        allocation_percent=demand_config.get("allocation_percent", 50),
        window_start=start,
        window_end=start
        + timedelta(weeks=demand_config.get("weeks", 2))
        - timedelta(days=1),
        description="合成协调需求",
    )
    db.add_all(
        [
            demand,
            CapacitySetting(
                org_id=org.id, enabled=True, updated_by_user_id=users[b_code].id
            ),
        ]
    )
    db.flush()
    for entry in case.get("assignments", []):
        assignment = CapacityAssignment(
            org_id=org.id,
            demand_id=demand.id,
            employee_id=employees[entry["code"]].id,
            allocation_percent=entry.get("percent", 40),
            start_date=start + timedelta(weeks=entry.get("start_week", 0))
            if entry.get("start_week") is not None
            else None,
            due_date=start + timedelta(weeks=entry.get("end_week", 1), days=6),
            assigned_by_user_id=users[b_code].id,
        )
        if entry.get("start_week") is None:
            assignment.created_at = utc_now() - timedelta(days=7)
        db.add(assignment)
    db.commit()
    auth = {
        label: {
            "Authorization": f"Bearer {create_access_token(users[code].id, expires_delta=timedelta(hours=1))}"
        }
        for label, code in (
            ("b_manager", b_code),
            ("a_manager", a_code),
            ("a_employee", a_employee_code),
        )
    }
    return org.id, employees, auth, demand, start


def _exercise(db: Session, client: TestClient, case: dict) -> dict:
    existing_users = set(db.exec(select(User.id)).all())
    org = Organization(slug=f"planning-eval-{uuid4().hex}", name="Planning eval")
    db.add(org)
    db.flush()
    org_id = org.id
    try:
        org_id, employees, auth, demand, start = _setup(db, case, org)
        before = business_rows(db, org_id)
        base = f"/api/v1/orgs/{org_id}/planning"
        created = client.post(
            f"{base}/demands/{demand.id}/cases", headers=auth["b_manager"]
        )
        assert created.status_code == 200, created.text
        result = created.json()
        case_url = f"{base}/cases/{result['id']}"
        assert client.get(case_url, headers=auth["b_manager"]).status_code == 200
        assert business_rows(db, org_id) == before
        options = {item["kind"]: item for item in result["options"]}
        for expected in case["expect"]["options"]:
            actual = options[expected["kind"]]
            assert actual["verdict"] == expected["verdict"]
            for code in expected.get("findings", []):
                assert code in {finding["code"] for finding in actual["findings"]}
        for kind in case["expect"].get("absent_kinds", []):
            assert kind not in options
        if "newcomer_required" in case["expect"]:
            assert ("newcomer" in options) == case["expect"]["newcomer_required"]
        if case["expect"].get("legacy_start"):
            candidate = next(
                p
                for p in result["data"]["candidates"]
                if p["employee_id"] == str(employees[case["expect"]["legacy_start"]].id)
            )
            assert candidate["weeks"][0]["legacy_start"] is True
        if case["expect"].get("small_borrow"):
            assert any(
                d["count"] is None and d["note"] == "人数较少"
                for d in options["borrow"]["params"]["departments"]
            )
        if "borrow" in options:
            borrowed = json.dumps(options["borrow"], ensure_ascii=False)
            assert not any(
                str(e.id) in borrowed or e.display_name in borrowed
                for e in employees.values()
            )
        for viewer in case["expect"].get("denied", []):
            assert (
                client.post(
                    f"{base}/demands/{demand.id}/cases", headers=auth[viewer]
                ).status_code
                == 404
            )
            assert client.get(case_url, headers=auth[viewer]).status_code == 404
            assert (
                client.post(
                    f"{case_url}/options", headers=auth[viewer], json={"assignees": []}
                ).status_code
                == 404
            )
        if custom := case.get("custom"):
            body = {
                "assignees": [
                    {
                        "employee_id": str(employees[custom["code"]].id),
                        "allocation_percent": custom.get("percent", 50),
                        "start_date": start.isoformat(),
                        "end_date": demand.window_end.isoformat(),
                    }
                ]
            }
            response = client.post(
                f"{case_url}/options", headers=auth["b_manager"], json=body
            )
            assert response.status_code == 200, response.text
            assert response.json()["verdict"] == custom["verdict"]
            assert custom["finding"] in {f["code"] for f in response.json()["findings"]}
        assert business_rows(db, org_id) == before
        if case["expect"].get("stale_after"):
            person = employees[case["expect"]["stale_after"]]
            person.employment_status = "inactive"
            db.add(person)
            db.commit()
            stale = client.get(case_url, headers=auth["b_manager"])
            assert stale.status_code == case["expect"]["stale_status"]
            assert (
                stale.json()["detail"] == "部分候选人已不在当前管辖范围内，请重新模拟"
            )
            response = client.post(
                f"{case_url}/options",
                headers=auth["b_manager"],
                json={
                    "assignees": [
                        {
                            "employee_id": str(person.id),
                            "allocation_percent": 50,
                            "start_date": start.isoformat(),
                            "end_date": demand.window_end.isoformat(),
                        }
                    ]
                },
            )
            assert (
                response.status_code == 409
                and response.json()["detail"] == "数据已变化，请重新模拟"
            )
            assert business_rows(db, org_id) == before
        return {
            "id": case["id"],
            "category": case["category"],
            "options": len(result["options"]),
            "verdicts": [o["verdict"] for o in result["options"]],
            "newcomer": "newcomer" in options,
        }
    finally:
        _cleanup(db, org_id, existing_users)


@pytest.mark.parametrize("path", CASES, ids=[path.stem for path in CASES])
def test_planning_eval(path: Path, db: Session, client: TestClient) -> None:
    case = yaml.safe_load(path.read_text())
    assert case["id"] == path.stem
    result = _exercise(db, client, case)
    if output := os.environ.get("PLANNING_EVAL_RESULT"):
        with Path(output).open("a") as stream:
            stream.write(json.dumps(result, ensure_ascii=False) + "\n")
