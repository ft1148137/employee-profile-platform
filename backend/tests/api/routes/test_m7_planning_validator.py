import importlib
from collections.abc import Generator
from datetime import date, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlmodel import Session, select

from app.authz.principal import Principal
from app.core.security import create_access_token
from app.domains.base import utc_now
from app.domains.capacity.calendar import loads, this_week
from app.domains.capacity.models import (
    CapacityAssignment,
    CapacityDemand,
    CapacitySetting,
    CapacityUnavailability,
    CapacityWeek,
)
from app.domains.org.models import Employee, OrgMembership, RoleGrant
from app.domains.planning.models import PlanningCase, PlanningOption, PlanningSnapshot
from app.domains.planning.snapshot import current_fingerprint
from app.domains.planning.validator import validate
from app.domains.profile.models import ProfileVersion

PERSON = str(uuid4())
OUTSIDER = str(uuid4())
START = date(2026, 11, 16)
END = date(2026, 11, 29)


def snapshot(
    *,
    level: str | None = "routine",
    in_basis: bool = True,
    status: str = "confirmed",
    other: int | None = 10,
    capacity: int = 100,
    employment: str = "active",
    headcount: int = 1,
) -> dict:
    return {
        "demand": {
            "minimum_level": "routine",
            "headcount": headcount,
            "allocation_percent": 50,
            "window_start": START.isoformat(),
            "window_end": END.isoformat(),
            "committed_end_date": END.isoformat(),
        },
        "rules": {
            "levels": ["beginner", "routine", "expert"],
            "minimum_percent": 10,
            "current_week": START.isoformat(),
        },
        "candidates": [
            {
                "employee_id": PERSON,
                "department_id": str(uuid4()),
                "level": level,
                "in_basis": in_basis,
                "newcomer": False,
                "employment_status": employment,
                "weeks": [
                    {
                        "week_start": d.isoformat(),
                        "capacity_percent": capacity,
                        "other_percent": other,
                        "platform_percent": 0,
                        "status": status,
                        "confirmed_at": None,
                        "legacy_start": False,
                    }
                    for d in (date(2026, 11, 9), START, date(2026, 11, 23))
                ],
            }
        ],
    }


def params(
    *,
    employee_id: str = PERSON,
    percent: int = 50,
    start: date = START,
    end: date = END,
) -> dict:
    return {
        "assignees": [
            {
                "employee_id": employee_id,
                "allocation_percent": percent,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
            }
        ]
    }


@pytest.mark.parametrize(
    ("data", "plan", "verdict", "code"),
    [
        (snapshot(), params(), "feasible", None),
        (snapshot(other=0), params(percent=100), "feasible", None),
        (
            snapshot(in_basis=False, level=None),
            params(),
            "conditional",
            "outside_basis",
        ),
        (snapshot(), params(start=date(2026, 11, 9)), "conditional", "outside_window"),
        (snapshot(), params(employee_id=OUTSIDER), "conditional", "borrow"),
        (snapshot(level="beginner"), params(), "infeasible", "level_too_low"),
        (snapshot(employment="inactive"), params(), "infeasible", "inactive"),
        (snapshot(other=70), params(), "infeasible", "over_capacity"),
        (snapshot(headcount=2), params(), "infeasible", "headcount_gap"),
        (snapshot(), params(start=END, end=START), "infeasible", "invalid_dates"),
        (
            snapshot(status="unconfirmed", other=None),
            params(),
            "insufficient_data",
            "unconfirmed",
        ),
        (snapshot(status="stale"), params(), "insufficient_data", "unconfirmed"),
        (snapshot(capacity=40), params(), "infeasible", "over_capacity"),
    ],
)
def test_verdict_rules(data: dict, plan: dict, verdict: str, code: str | None) -> None:
    result = validate(data, plan)
    assert result.verdict == verdict
    if code:
        assert code in {finding["code"] for finding in result.findings}
    assert set(result.summary) == {
        "finish_date",
        "covered_headcount",
        "gap_person_weeks",
        "conditions",
        "newcomer_count",
        "affected_assignment_ids",
        "approvers",
    }


def test_weekly_headcount_and_gap() -> None:
    data = snapshot()
    result = validate(data, params(end=START))
    assert result.verdict == "infeasible"
    assert result.summary["covered_headcount"] == 0
    assert result.summary["gap_person_weeks"] == 0.5
    assert any(
        f["code"] == "headcount_gap" and f["week_start"] == "2026-11-23"
        for f in result.findings
    )
    reduced = validate(data, params(percent=10))
    assert reduced.verdict == "conditional"
    assert reduced.summary["gap_person_weeks"] == 0.8


def test_capacity_checks_ignore_past_weeks() -> None:
    data = snapshot()
    data["rules"]["current_week"] = date(2026, 11, 23).isoformat()
    data["candidates"][0]["weeks"] = data["candidates"][0]["weeks"][-1:]
    result = validate(data, params())
    assert result.verdict == "feasible"
    assert not any(f["code"] == "unconfirmed" for f in result.findings)

    data["candidates"][0]["weeks"] = []
    result = validate(data, params(end=START))
    assert result.verdict == "infeasible"  # Headcount still covers the demand window.
    assert not any(f["code"] == "unconfirmed" for f in result.findings)


def test_future_capacity_still_requires_confirmation() -> None:
    data = snapshot(status="unconfirmed", other=None)
    data["rules"]["current_week"] = date(2026, 11, 23).isoformat()
    result = validate(data, params())
    assert result.verdict == "insufficient_data"
    assert {f["week_start"] for f in result.findings if f["code"] == "unconfirmed"} == {
        "2026-11-23"
    }


@pytest.fixture
def planning_setup(
    minimal_demo: tuple, db: Session, client: TestClient
) -> Generator[tuple]:
    url, employees, auth = minimal_demo
    org_id = employees["E01"].org_id
    members = {
        m.employee_id: m
        for m in db.exec(
            select(OrgMembership).where(OrgMembership.org_id == org_id)
        ).all()
    }
    admin = members[None].user_id
    b = members[employees["E05"].id]
    auth["b"] = {
        "Authorization": f"Bearer {create_access_token(b.user_id, expires_delta=timedelta(minutes=10))}"
    }
    target = employees["E04"].department_id
    start = this_week(db, org_id)
    capability = client.get(f"{url}/capabilities", headers=auth["admin"]).json()[
        "data"
    ][0]
    demand = CapacityDemand(
        org_id=org_id,
        requesting_department_id=employees["E01"].department_id,
        requester_user_id=members[employees["E01"].id].user_id,
        target_department_id=target,
        capability_key=capability["key"],
        capability_name=capability["name"],
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
                membership_id=b.id,
                role="dept_manager",
                department_id=target,
            ),
            CapacitySetting(org_id=org_id, enabled=True, updated_by_user_id=admin),
            demand,
        ]
    )
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
    yield url, employees, auth, demand
    db.rollback()
    for model in (
        PlanningOption,
        PlanningSnapshot,
        PlanningCase,
        CapacityAssignment,
        CapacityUnavailability,
        CapacityWeek,
        CapacityDemand,
        CapacitySetting,
        RoleGrant,
    ):
        db.execute(delete(model).where(model.org_id == org_id))
    db.commit()


@pytest.fixture
def planning_case(planning_setup: tuple, client: TestClient) -> tuple:
    return *planning_setup, _create_case(planning_setup, client)


def _create_case(planning_setup: tuple, client: TestClient) -> dict:
    url, _, auth, demand = planning_setup
    result = client.post(f"{url}/planning/demands/{demand.id}/cases", headers=auth["b"])
    assert result.status_code == 200, result.text
    return result.json()


def test_snapshot_starts_at_current_week_and_week_rollover_stales_case(
    planning_setup: tuple,
    client: TestClient,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url, employees, auth, demand = planning_setup
    current = demand.window_start
    demand.window_start = current - timedelta(weeks=2)
    db.commit()
    created = client.post(
        f"{url}/planning/demands/{demand.id}/cases", headers=auth["b"]
    )
    assert created.status_code == 200, created.text
    case = created.json()
    assert case["data"]["rules"]["current_week"] == current.isoformat()
    assert all(
        person["weeks"][0]["week_start"] == current.isoformat()
        for person in case["data"]["candidates"]
    )
    case_url = f"{url}/planning/cases/{case['id']}"
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
    option = client.post(f"{case_url}/options", headers=auth["b"], json=body)
    assert option.status_code == 200, option.text
    assert not any(f["code"] == "unconfirmed" for f in option.json()["findings"])

    def next_week(_session: Session, _org_id: UUID) -> date:
        return current + timedelta(weeks=1)

    monkeypatch.setattr(
        importlib.import_module("app.domains.planning.snapshot"), "this_week", next_week
    )
    assert client.get(case_url, headers=auth["b"]).json()["stale"] is True
    assert (
        client.post(f"{case_url}/options", headers=auth["b"], json=body).status_code
        == 409
    )


def test_case_options_and_privacy(
    planning_case: tuple, client: TestClient, db: Session
) -> None:
    url, employees, auth, demand, case = planning_case
    case_url = f"{url}/planning/cases/{case['id']}"
    assert (
        case["stale"] is False
        and [o for o in case["options"] if o["kind"] == "custom"] == []
    )
    assert "statement" not in str(case["data"]).lower()
    assert "evidence" not in str(case["data"]).lower()
    refreshed = client.post(
        f"{url}/planning/demands/{demand.id}/cases", headers=auth["b"]
    ).json()
    assert refreshed["id"] == case["id"]
    assert refreshed["snapshot_id"] != case["snapshot_id"]
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
    option = client.post(f"{case_url}/options", headers=auth["b"], json=body)
    assert option.status_code == 200, option.text
    assert option.json()["kind"] == "custom"
    assert (
        len(
            [
                o
                for o in client.get(case_url, headers=auth["b"]).json()["options"]
                if o["kind"] == "custom"
            ]
        )
        == 1
    )
    third = client.post(
        f"{url}/planning/demands/{demand.id}/cases", headers=auth["b"]
    ).json()
    assert third["snapshot_id"] != refreshed["snapshot_id"]
    assert [
        o
        for o in client.get(case_url, headers=auth["b"]).json()["options"]
        if o["kind"] == "custom"
    ] == []
    for role in ("manager", "employee", "foreign"):
        assert client.get(case_url, headers=auth[role]).status_code == 404
        assert (
            client.post(
                f"{case_url}/options", headers=auth[role], json=body
            ).status_code
            == 404
        )
        assert (
            client.post(
                f"{url}/planning/demands/{demand.id}/cases", headers=auth[role]
            ).status_code
            == 404
        )
    assert (
        client.post(f"{case_url}/options", headers=auth["manager"], json={}).status_code
        == 404
    )
    setting = db.exec(
        select(CapacitySetting).where(CapacitySetting.org_id == demand.org_id)
    ).one()
    setting.enabled = False
    db.commit()
    assert client.get(case_url, headers=auth["b"]).status_code == 404
    assert (
        client.post(f"{case_url}/options", headers=auth["b"], json=body).status_code
        == 404
    )
    assert (
        client.post(
            f"{url}/planning/demands/{demand.id}/cases", headers=auth["b"]
        ).status_code
        == 404
    )


@pytest.mark.parametrize(
    "change",
    [
        "week",
        "confirmation",
        "unavailability",
        "assignment",
        "inactive",
        "revision",
        "profile_version",
    ],
)
def test_stale_inputs_reject_option(
    planning_setup: tuple, db: Session, client: TestClient, change: str
) -> None:
    url, employees, auth, demand = planning_setup
    person = employees["E04"]
    if change == "profile_version":
        _publish_initial_profile(
            client, url, auth["admin"], person, demand.capability_key
        )
    case = _create_case(planning_setup, client)
    if change in {"week", "confirmation"}:
        week = db.exec(
            select(CapacityWeek).where(
                CapacityWeek.org_id == demand.org_id,
                CapacityWeek.employee_id == person.id,
            )
        ).first()
        assert week is not None
        if change == "week":
            week.other_percent = 20
        else:
            week.confirmed_at = utc_now() - timedelta(days=1)
    elif change == "unavailability":
        db.add(
            CapacityUnavailability(
                org_id=demand.org_id,
                employee_id=person.id,
                start_date=demand.window_start,
                end_date=demand.window_start,
                created_by_user_id=demand.requester_user_id,
            )
        )
    elif change == "assignment":
        db.add(
            CapacityAssignment(
                org_id=demand.org_id,
                demand_id=demand.id,
                employee_id=person.id,
                start_date=demand.window_start,
                due_date=demand.window_end,
                allocation_percent=10,
                assigned_by_user_id=demand.requester_user_id,
            )
        )
    elif change == "inactive":
        person.employment_status = "inactive"
    elif change == "profile_version":
        previous = db.exec(
            select(ProfileVersion).where(
                ProfileVersion.org_id == demand.org_id,
                ProfileVersion.subject_employee_id == person.id,
            )
        ).one()
        db.add(
            ProfileVersion(
                org_id=demand.org_id,
                subject_employee_id=person.id,
                revision=previous.revision + 1,
                schema_version_id=previous.schema_version_id,
                snapshot=previous.snapshot,
                published_at=utc_now(),
                published_by={"actor_type": "system_worker"},
                supersedes_version_id=previous.id,
            )
        )
    else:
        demand.revision += 1
    db.commit()
    case_url = f"{url}/planning/cases/{case['id']}"
    current = client.get(case_url, headers=auth["b"])
    if change == "inactive":
        assert current.status_code == 409
    else:
        assert current.status_code == 200
        assert current.json()["stale"] is True
    body = {
        "assignees": [
            {
                "employee_id": str(person.id),
                "allocation_percent": 50,
                "start_date": demand.window_start.isoformat(),
                "end_date": demand.window_end.isoformat(),
            }
        ]
    }
    response = client.post(f"{case_url}/options", headers=auth["b"], json=body)
    assert response.status_code == 409
    assert response.json()["detail"] == "数据已变化，请重新模拟"


def test_expired_week_confirmation_stales_case(
    planning_setup: tuple,
    db: Session,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url, employees, auth, demand = planning_setup
    confirmed_at = utc_now() - timedelta(days=27)
    for week in db.exec(
        select(CapacityWeek).where(CapacityWeek.org_id == demand.org_id)
    ).all():
        week.confirmed_at = confirmed_at
    db.commit()
    created = client.post(
        f"{url}/planning/demands/{demand.id}/cases", headers=auth["b"]
    )
    assert created.status_code == 200, created.text
    case = created.json()
    assert case["stale"] is False
    assert all(
        week["status"] == "confirmed"
        for person in case["data"]["candidates"]
        for week in person["weeks"][:2]
        if week["confirmed_at"] is not None
    )
    principal = Principal.system_worker(demand.org_id)
    before = current_fingerprint(db, principal, demand)
    assert before == current_fingerprint(db, principal, demand)
    case_url = f"{url}/planning/cases/{case['id']}"
    monkeypatch.setattr(
        importlib.import_module("app.domains.planning.snapshot"),
        "this_week",
        lambda _session, _org_id: demand.window_start,
    )
    monkeypatch.setattr(
        importlib.import_module("app.domains.capacity.calendar"),
        "utc_now",
        lambda: confirmed_at + timedelta(days=29),
    )
    monkeypatch.setattr(
        importlib.import_module("app.domains.planning.snapshot"),
        "utc_now",
        lambda: confirmed_at + timedelta(days=29),
    )
    assert (
        loads(db, demand.org_id, [employees["E04"].id], demand.window_start, 1)[
            employees["E04"].id
        ][0].status
        == "stale"
    )
    assert client.get(case_url, headers=auth["b"]).json()["stale"] is True
    assert (
        client.post(
            f"{case_url}/options",
            headers=auth["b"],
            json=params(
                employee_id=str(employees["E04"].id),
                start=demand.window_start,
                end=demand.window_end,
            ),
        ).status_code
        == 409
    )


def _publish_initial_profile(
    client: TestClient,
    url: str,
    headers: dict[str, str],
    employee: Employee,
    capability_key: str,
) -> None:
    response = client.post(
        f"{url}/employees/{employee.id}/profile/direct-edits",
        headers=headers,
        json={
            "base_revision": None,
            "items": [
                {
                    "op": "add",
                    "capability_key": capability_key,
                    "statement": "合成能力画像",
                    "level": "routine",
                }
            ],
        },
    )
    assert response.status_code == 200, response.text


def test_external_department_count_stales_borrow_case(
    planning_setup: tuple, db: Session, client: TestClient
) -> None:
    url, employees, auth, demand = planning_setup
    demand.capability_key = "backend_api"
    db.commit()
    for code in ("E01", "E02"):
        _publish_initial_profile(
            client, url, auth["admin"], employees[code], demand.capability_key
        )
    case = _create_case(planning_setup, client)
    assert any(option["kind"] == "borrow" for option in case["options"])
    assert case["stale"] is False
    source = db.exec(
        select(ProfileVersion).where(
            ProfileVersion.org_id == demand.org_id,
            ProfileVersion.subject_employee_id == employees["E01"].id,
            ProfileVersion.revision == 1,
        )
    ).one()
    db.add(
        ProfileVersion(
            org_id=demand.org_id,
            subject_employee_id=employees["E08"].id,
            revision=1,
            schema_version_id=source.schema_version_id,
            snapshot=source.snapshot,
            published_at=utc_now(),
            published_by={"actor_type": "system_worker"},
        )
    )
    db.commit()
    case_url = f"{url}/planning/cases/{case['id']}"
    assert client.get(case_url, headers=auth["b"]).json()["stale"] is True
    assert (
        client.post(
            f"{case_url}/options",
            headers=auth["b"],
            json=params(
                employee_id=str(employees["E04"].id),
                start=demand.window_start,
                end=demand.window_end,
            ),
        ).status_code
        == 409
    )
