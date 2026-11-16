from datetime import UTC, datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.domains.capacity.calendar import this_week
from tests.api.routes.test_m6_capacity_workload import (
    capacity as capacity,
)
from tests.api.routes.test_m6_capacity_workload import (
    enable,
    week_body,
)


def test_missing_weeks_are_unconfirmed(capacity: tuple, client: TestClient) -> None:
    url, _, auth = capacity
    enable(client, capacity)
    response = client.get(f"{url}/capacity/workloads/me", headers=auth["employee"])
    assert response.status_code == 200
    row = response.json()
    assert len(row["weeks"]) == 8
    assert all(w["status"] == "unconfirmed" for w in row["weeks"])
    assert row["band"] == "unconfirmed" and row["last_confirmed"] is None
    assert "有余量" not in response.text and "有空" not in response.text


@pytest.mark.parametrize(
    "params",
    [
        {"from_week": "2026-11-17"},
        {"weeks": 0},
        {"weeks": 27},
    ],
)
def test_invalid_read_window(capacity: tuple, client: TestClient, params: dict) -> None:
    url, _, auth = capacity
    enable(client, capacity)
    for route, role in [("workloads/me", "employee"), ("workloads", "manager")]:
        assert (
            client.get(
                f"{url}/capacity/{route}", params=params, headers=auth[role]
            ).status_code
            == 422
        )


def test_batch_is_atomic(capacity: tuple, db: Session, client: TestClient) -> None:
    url, employees, auth = capacity
    enable(client, capacity)
    body = week_body(employees["E02"].id, 50)
    body["end_week"] = (
        this_week(db, employees["E02"].org_id) + timedelta(weeks=3)
    ).isoformat()
    body["items"].append(
        {"employee_id": str(employees["E03"].id), "other_percent": 200}
    )
    bad = {
        **body,
        "items": [
            *body["items"],
            {"employee_id": str(employees["E04"].id), "other_percent": 20},
        ],
    }
    assert (
        client.put(
            f"{url}/capacity/weeks", json=bad, headers=auth["manager"]
        ).status_code
        == 404
    )
    own = client.get(f"{url}/capacity/workloads/me", headers=auth["employee"]).json()
    assert all(w["other_percent"] is None for w in own["weeks"])
    result = client.put(f"{url}/capacity/weeks", json=body, headers=auth["manager"])
    assert result.status_code == 200, result.text
    assert result.json()["count"] == 2
    for row in result.json()["data"]:
        assert all(w["status"] == "confirmed" for w in row["weeks"][:4])
        assert all(w["status"] == "unconfirmed" for w in row["weeks"][4:])


def test_calendar_capacity_freshness_and_overlaps(
    capacity: tuple, db: Session, client: TestClient
) -> None:
    from app.domains.base import utc_now
    from app.domains.capacity.calendar import loads, shortfalls
    from app.domains.capacity.models import (
        CapacityAssignment,
        CapacityDemand,
        CapacityUnavailability,
        CapacityWeek,
    )
    from tests.api.routes.test_m6_capacity_workload import user_id

    url, employees, auth = capacity
    enable(client, capacity)
    person = employees["E02"]
    start = this_week(db, person.org_id)
    actor = user_id(db, employees["E01"])
    db.add(
        CapacityWeek(
            org_id=person.org_id,
            employee_id=person.id,
            week_start=start,
            other_percent=50,
            confirmed_by_user_id=actor,
            confirmed_at=utc_now() - timedelta(days=29),
        )
    )
    demand = CapacityDemand(
        org_id=person.org_id,
        requesting_department_id=person.department_id,
        target_department_id=person.department_id,
        requester_user_id=actor,
        capability_key="synthetic",
        capability_name="合成能力",
        headcount=1,
        allocation_percent=40,
        window_start=start,
        window_end=start + timedelta(weeks=4),
        description="合成需求",
    )
    db.add(demand)
    db.flush()
    assignment = CapacityAssignment(
        org_id=person.org_id,
        demand_id=demand.id,
        employee_id=person.id,
        allocation_percent=40,
        start_date=start + timedelta(days=4),
        due_date=start + timedelta(weeks=1),
        assigned_by_user_id=actor,
    )
    db.add(assignment)
    unavailable = CapacityUnavailability(
        org_id=person.org_id,
        employee_id=person.id,
        start_date=start,
        end_date=start + timedelta(days=1),
        created_by_user_id=actor,
    )
    db.add(unavailable)
    db.commit()
    try:
        row = client.get(
            f"{url}/capacity/workloads/me", headers=auth["employee"]
        ).json()
        current = row["weeks"][0]
        assert (
            current["status"],
            current["capacity_percent"],
            current["platform_percent"],
            current["legacy_start"],
        ) == ("stale", 60, 40, False)
        assert row["band"] == "unconfirmed" and row["confirmed_percent"] == 90
        computed = loads(db, person.org_id, [person.id], start, 3)[person.id]
        assert [w.platform_percent for w in computed] == [40, 40, 0]
        assert shortfalls(computed, 10) == (
            [w.week_start for w in computed],
            [(start, 40)],
        )
        assert (
            loads(
                db,
                person.org_id,
                [person.id],
                start,
                1,
                exclude_assignment_id=assignment.id,
            )[person.id][0].platform_percent
            == 0
        )
        assignment.start_date = None
        assignment.created_at = datetime.combine(
            start - timedelta(weeks=1), time(12), tzinfo=UTC
        )
        db.add(assignment)
        db.commit()
        legacy = loads(db, person.org_id, [person.id], start - timedelta(weeks=2), 3)[
            person.id
        ]
        assert [(w.platform_percent, w.legacy_start) for w in legacy] == [
            (0, False),
            (40, True),
            (40, True),
        ]
    finally:
        db.delete(unavailable)
        db.commit()


@pytest.mark.parametrize("ranged", [False, True])
def test_approved_requests_only_set_requested_weeks(
    capacity: tuple, db: Session, client: TestClient, ranged: bool
) -> None:
    from app.domains.capacity.models import CapacityWorkloadRequest

    url, people, auth = capacity
    enable(client, capacity)
    start = this_week(db, people["E02"].org_id)
    request = CapacityWorkloadRequest(
        org_id=people["E02"].org_id,
        employee_id=people["E02"].id,
        requested_percent=60,
        reason="合成申请",
        start_week=start + timedelta(weeks=1) if ranged else None,
        end_week=start + timedelta(weeks=2) if ranged else None,
    )
    db.add(request)
    db.commit()
    response = client.post(
        f"{url}/capacity/workload-requests/{request.id}/decision",
        headers=auth["manager"],
        json={"approve": True},
    )
    assert response.status_code == 200, response.text
    row = client.get(f"{url}/capacity/workloads/me", headers=auth["employee"]).json()
    assert [w["other_percent"] for w in row["weeks"]] == (
        [None, 60, 60, None, None, None, None, None]
        if ranged
        else [60, None, None, None, None, None, None, None]
    )
    future = client.get(
        f"{url}/capacity/workloads/me",
        params={"from_week": (start + timedelta(weeks=1)).isoformat(), "weeks": 2},
        headers=auth["employee"],
    ).json()
    assert len(future["weeks"]) == 2 and future["base_percent"] == row["base_percent"]


def test_enterprise_week_uses_local_today(
    capacity: tuple, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, date, datetime

    from app.domains.capacity import calendar
    from app.domains.org.models import Organization

    _, people, _ = capacity
    org = db.get(Organization, people["E02"].org_id)
    assert org is not None
    org.timezone = "Pacific/Honolulu"
    db.add(org)
    db.commit()
    monkeypatch.setattr(
        calendar, "utc_now", lambda: datetime(2026, 11, 16, 1, tzinfo=UTC)
    )
    assert calendar.this_week(db, org.id) == date(2026, 11, 9)
