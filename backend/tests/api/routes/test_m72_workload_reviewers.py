from datetime import timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.domains.base import utc_now
from app.domains.capacity.calendar import this_week
from app.domains.capacity.workload import WORKLOAD_REVIEW_NOTICE
from app.domains.inbox.models import InboxItem
from app.domains.review.models import ReviewDelegation
from tests.api.routes.test_m6_capacity_workload import capacity as capacity
from tests.api.routes.test_m6_capacity_workload import enable, user_id


def test_deleting_another_employees_unavailability_notifies_them(
    capacity: tuple, db: Session, client: TestClient
) -> None:
    url, people, auth = capacity
    enable(client, capacity)
    subject = people["E02"]
    endpoint = f"{url}/capacity/unavailability"
    day = this_week(db, subject.org_id).isoformat()
    body = {"employee_id": str(subject.id), "start_date": day, "end_date": day}
    own = client.post(endpoint, headers=auth["employee"], json=body)
    delegated = client.post(endpoint, headers=auth["manager"], json=body)
    assert own.status_code == delegated.status_code == 200

    def notifications() -> int:
        return len(
            db.exec(
                select(InboxItem).where(
                    InboxItem.org_id == subject.org_id,
                    InboxItem.recipient_employee_id == subject.id,
                )
            ).all()
        )

    before = notifications()
    assert (
        client.delete(
            f"{endpoint}/{own.json()['id']}", headers=auth["employee"]
        ).status_code
        == 200
    )
    assert notifications() == before
    assert (
        client.delete(
            f"{endpoint}/{delegated.json()['id']}", headers=auth["manager"]
        ).status_code
        == 200
    )
    assert notifications() == before + 1


def test_workload_reviewers_match_notification_recipients(
    capacity: tuple, db: Session, client: TestClient
) -> None:
    url, people, auth = capacity
    enable(client, capacity)
    subject = people["E02"]
    manager_id = user_id(db, people["E01"])
    delegate_id = user_id(db, people["E05"])
    db.add(
        ReviewDelegation(
            org_id=subject.org_id,
            manager_user_id=manager_id,
            delegate_user_id=delegate_id,
            created_by_user_id=manager_id,
            starts_at=utc_now() - timedelta(hours=1),
            ends_at=utc_now() + timedelta(hours=1),
        )
    )
    db.commit()
    mine = f"{url}/capacity/workloads/me"
    response = client.get(mine, headers=auth["employee"])
    assert response.status_code == 200, response.text
    expected = [people["E01"].display_name, people["E05"].display_name]
    assert set(response.json()["reviewer_names"]) == set(expected)
    week = this_week(db, subject.org_id).isoformat()
    submitted = client.post(
        f"{mine}/requests",
        headers=auth["employee"],
        json={
            "requested_percent": 50,
            "reason": "合成调整原因",
            "start_week": week,
            "end_week": week,
        },
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["reviewer_names"] == response.json()["reviewer_names"]
    recipients = db.exec(
        select(InboxItem.recipient_user_id).where(
            InboxItem.org_id == subject.org_id,
            InboxItem.message == WORKLOAD_REVIEW_NOTICE,
        )
    ).all()
    assert set(recipients) == {manager_id, delegate_id}


def test_manager_cannot_be_their_own_workload_reviewer(
    capacity: tuple, db: Session, client: TestClient
) -> None:
    url, people, auth = capacity
    enable(client, capacity)
    manager = people["E01"]
    mine = f"{url}/capacity/workloads/me"
    result = client.get(mine, headers=auth["manager"])
    assert result.status_code == 200, result.text
    assert manager.display_name not in result.json()["reviewer_names"]
    week = this_week(db, manager.org_id).isoformat()
    submitted = client.post(
        f"{mine}/requests",
        headers=auth["manager"],
        json={
            "requested_percent": 50,
            "reason": "合成调整原因",
            "start_week": week,
            "end_week": week,
        },
    )
    assert submitted.status_code == 200, submitted.text
    assert manager.display_name not in submitted.json()["reviewer_names"]
    assert (
        db.exec(
            select(InboxItem).where(
                InboxItem.org_id == manager.org_id,
                InboxItem.recipient_user_id == user_id(db, manager),
                InboxItem.message == WORKLOAD_REVIEW_NOTICE,
            )
        ).first()
        is None
    )
