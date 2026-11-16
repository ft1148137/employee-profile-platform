from collections.abc import Generator
from datetime import UTC, datetime, time, timedelta

import pytest
from sqlalchemy import delete
from sqlmodel import Session, select

from app.domains.base import utc_now
from app.domains.capacity.calendar import this_week
from app.domains.capacity.models import (
    CapacityAssignment,
    CapacityExtensionRequest,
    CapacityWeek,
)
from app.domains.review.models import ReviewDelegation
from tests.api.routes.test_m6_capacity_assignment import case as case


@pytest.fixture
def past_case(case: tuple, db: Session) -> Generator[tuple]:
    api, _, demand, people, users, org = case
    start = this_week(db, org.id)
    demand.committed_start_date = None
    demand.committed_end_date = start + timedelta(weeks=2, days=4)
    db.add(demand)
    db.execute(delete(CapacityWeek).where(CapacityWeek.org_id == org.id))
    for offset in range(3):
        db.add(
            CapacityWeek(
                org_id=org.id,
                employee_id=people["w"].id,
                week_start=start + timedelta(weeks=offset),
                other_percent=0,
                confirmed_by_user_id=users["bm"].id,
            )
        )
    db.add(
        ReviewDelegation(
            org_id=org.id,
            manager_user_id=users["cm"].id,
            delegate_user_id=users["bm"].id,
            created_by_user_id=users["cm"].id,
            starts_at=utc_now() - timedelta(hours=1),
            ends_at=utc_now() + timedelta(hours=1),
        )
    )
    db.commit()
    yield case
    db.rollback()
    db.execute(
        delete(CapacityExtensionRequest).where(
            CapacityExtensionRequest.org_id == org.id
        )
    )
    db.commit()


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("write", ["extension", "due", "start"])
def test_started_assignment_ignores_past_weeks(
    past_case: tuple, db: Session, legacy: bool, write: str
) -> None:
    api, _, demand, people, users, org = past_case
    current = this_week(db, org.id)
    past = current - timedelta(weeks=3)
    row = CapacityAssignment(
        org_id=org.id,
        demand_id=demand.id,
        employee_id=people["w"].id,
        assigned_by_user_id=users["bm"].id,
        allocation_percent=20,
        start_date=None if legacy else past,
        due_date=current + timedelta(days=4),
        created_at=datetime.combine(past, time(12), tzinfo=UTC),
    )
    db.add(row)
    db.add(
        CapacityWeek(
            org_id=org.id,
            employee_id=people["w"].id,
            week_start=past,
            other_percent=150,
            confirmed_by_user_id=users["bm"].id,
        )
    )
    db.commit()
    if write == "extension":
        pending = api(
            f"/assignments/{row.id}/extension-requests",
            "w",
            {
                "requested_due_date": (
                    current + timedelta(weeks=1, days=4)
                ).isoformat(),
                "reason": "合成延期",
            },
        )
        assert pending["status"] == "pending"
        decided = api(
            f"/extension-requests/{pending['id']}/decision",
            "bm",
            {"approve": True},
        )
        assert decided["status"] == "approved"
    elif write == "due":
        api(
            f"/assignments/{row.id}",
            body={"due_date": (current + timedelta(weeks=1, days=4)).isoformat()},
            method="PATCH",
        )
    else:
        api(
            f"/assignments/{row.id}",
            body={"start_date": past.isoformat()},
            method="PATCH",
        )
    db.refresh(row)
    assert row.start_date == (past if write == "start" or not legacy else None)
    expected_due = current + (
        timedelta(days=4) if write == "start" else timedelta(weeks=1, days=4)
    )
    assert row.due_date == expected_due


def test_create_from_past_checks_only_current_and_future(
    past_case: tuple, db: Session
) -> None:
    api, _, demand, people, _, org = past_case
    current = this_week(db, org.id)
    past = current - timedelta(weeks=3)
    body = {
        "employee_id": str(people["w"].id),
        "allocation_percent": 20,
        "start_date": past.isoformat(),
        "due_date": (current + timedelta(weeks=1, days=4)).isoformat(),
        "outside_basis": True,
    }
    db.add(
        CapacityWeek(
            org_id=org.id,
            employee_id=people["w"].id,
            week_start=past,
            other_percent=150,
            confirmed_by_user_id=past_case[4]["bm"].id,
        )
    )
    db.commit()
    created = api(f"/demands/{demand.id}/assignments", body=body)
    assert created["start_date"] == past.isoformat()


@pytest.mark.parametrize("problem", ["unconfirmed", "over"])
def test_create_from_past_still_checks_current_week(
    past_case: tuple, db: Session, problem: str
) -> None:
    api, _, demand, people, _, org = past_case
    current = this_week(db, org.id)
    week = db.exec(
        select(CapacityWeek).where(
            CapacityWeek.org_id == org.id,
            CapacityWeek.employee_id == people["w"].id,
            CapacityWeek.week_start == current,
        )
    ).one()
    if problem == "unconfirmed":
        db.delete(week)
    else:
        week.other_percent = 90
        db.add(week)
    db.commit()
    detail = api(
        f"/demands/{demand.id}/assignments",
        body={
            "employee_id": str(people["w"].id),
            "allocation_percent": 20,
            "start_date": (current - timedelta(weeks=3)).isoformat(),
            "due_date": (current + timedelta(weeks=1, days=4)).isoformat(),
            "outside_basis": True,
        },
        status=409,
    )["detail"]
    if problem == "unconfirmed":
        assert detail["weeks"] == [
            {"week_start": current.isoformat(), "status": "unconfirmed"}
        ]
        assert detail["over_weeks"] == []
    else:
        assert detail["weeks"] == []
        assert detail["over_weeks"] == [
            {"week_start": current.isoformat(), "over_percent": 10}
        ]


def test_due_date_entirely_past_needs_no_capacity_records(
    past_case: tuple, db: Session
) -> None:
    api, _, demand, people, users, org = past_case
    current = this_week(db, org.id)
    past = current - timedelta(weeks=3)
    row = CapacityAssignment(
        org_id=org.id,
        demand_id=demand.id,
        employee_id=people["w"].id,
        assigned_by_user_id=users["bm"].id,
        allocation_percent=20,
        start_date=past,
        due_date=past + timedelta(days=2),
    )
    db.add(row)
    db.commit()
    result = api(
        f"/assignments/{row.id}",
        body={"due_date": (past + timedelta(days=4)).isoformat()},
        method="PATCH",
    )
    assert result["due_date"] == (past + timedelta(days=4)).isoformat()
