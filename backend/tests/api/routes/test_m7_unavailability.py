from collections.abc import Generator
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete

from app.domains.capacity.calendar import this_week
from app.domains.capacity.models import CapacityUnavailability
from tests.api.routes import test_m6_capacity_mcp, test_m6_capacity_workload
from tests.api.routes.test_m6_capacity_workload import enable

workload_capacity = test_m6_capacity_workload.capacity
mcp_capacity = test_m6_capacity_mcp.capacity


@pytest.fixture
def capacity(workload_capacity: tuple, db: Session) -> Generator[tuple]:
    yield workload_capacity
    db.rollback()
    db.exec(
        delete(CapacityUnavailability).where(
            CapacityUnavailability.org_id == workload_capacity[1]["E02"].org_id
        )
    )
    db.commit()


def test_self_unavailability(capacity: tuple, db: Session, client: TestClient) -> None:
    url, people, auth = capacity
    enable(client, capacity)
    start = this_week(db, people["E02"].org_id)
    endpoint = f"{url}/capacity"
    body = {
        "employee_id": str(people["E02"].id),
        "start_date": start.isoformat(),
        "end_date": (start + timedelta(days=1)).isoformat(),
    }
    result = client.post(
        f"{endpoint}/unavailability", headers=auth["employee"], json=body
    )
    assert result.status_code == 200, result.text
    row = result.json()
    assert row["over_weeks"] == [] and "reason" not in row
    assert (
        client.get(f"{endpoint}/workloads/me", headers=auth["employee"]).json()[
            "weeks"
        ][0]["capacity_percent"]
        == 60
    )
    params = {
        "employee_id": body["employee_id"],
        "from": start.isoformat(),
        "to": (start + timedelta(days=6)).isoformat(),
    }
    listing = client.get(
        f"{endpoint}/unavailability", headers=auth["employee"], params=params
    )
    assert listing.json()["data"][0]["id"] == row["id"]
    assert (
        client.delete(
            f"{endpoint}/unavailability/{row['id']}", headers=auth["employee"]
        ).status_code
        == 200
    )
    assert (
        client.get(f"{endpoint}/workloads/me", headers=auth["employee"]).json()[
            "weeks"
        ][0]["capacity_percent"]
        == 100
    )


@pytest.mark.parametrize(
    "role,code,status",
    [
        ("manager", "E02", 200),
        ("manager", "E04", 404),
        ("employee", "E03", 404),
        ("foreign", "E02", 404),
        ("admin", "E02", 403),
    ],
)
def test_unavailability_scope(
    capacity: tuple, db: Session, client: TestClient, role: str, code: str, status: int
) -> None:
    url, people, auth = capacity
    enable(client, capacity)
    start = this_week(db, people["E02"].org_id).isoformat()
    body = {"employee_id": str(people[code].id), "start_date": start, "end_date": start}
    endpoint = f"{url}/capacity/unavailability"
    result = client.post(endpoint, headers=auth[role], json=body)
    assert result.status_code == status, result.text
    if status == 200:
        row = result.json()
        params = {"employee_id": body["employee_id"], "from": start, "to": start}
        for denied in ("foreign",):
            assert (
                client.get(endpoint, headers=auth[denied], params=params).status_code
                == 404
            )
            assert (
                client.delete(
                    f"{endpoint}/{row['id']}", headers=auth[denied]
                ).status_code
                == 404
            )
        assert (
            client.get(endpoint, headers=auth["admin"], params=params).json()["data"][
                0
            ]["can_delete"]
            is False
        )
        assert (
            client.delete(f"{endpoint}/{row['id']}", headers=auth["admin"]).status_code
            == 403
        )
        assert (
            client.delete(
                f"{endpoint}/{row['id']}", headers=auth["employee"]
            ).status_code
            == 403
        )
        assert (
            client.delete(
                f"{endpoint}/{row['id']}", headers=auth["manager"]
            ).status_code
            == 200
        )


def test_admin_with_employee_account_is_read_only(
    capacity: tuple, db: Session, client: TestClient
) -> None:
    from sqlmodel import select

    from app.domains.org.models import OrgMembership, Role, RoleGrant

    url, people, auth = capacity
    enable(client, capacity)
    subject = people["E02"]
    start = this_week(db, subject.org_id).isoformat()
    endpoint = f"{url}/capacity/unavailability"
    body = {"employee_id": str(subject.id), "start_date": start, "end_date": start}
    row = client.post(endpoint, headers=auth["employee"], json=body).json()
    member = db.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == subject.org_id,
            OrgMembership.employee_id == subject.id,
        )
    ).one()
    db.add(
        RoleGrant(org_id=subject.org_id, membership_id=member.id, role=Role.ORG_ADMIN)
    )
    db.commit()
    params = {"employee_id": str(subject.id), "from": start, "to": start}
    listing = client.get(endpoint, headers=auth["employee"], params=params)
    assert listing.status_code == 200
    assert listing.json()["data"][0]["can_delete"] is False
    assert client.post(endpoint, headers=auth["employee"], json=body).status_code == 403
    assert (
        client.delete(f"{endpoint}/{row['id']}", headers=auth["employee"]).status_code
        == 403
    )


def test_unavailability_overload_audit_and_inactive(
    capacity: tuple, db: Session, client: TestClient
) -> None:
    from sqlmodel import select

    from app.domains.audit.models import AuditEvent
    from app.domains.capacity.models import CapacityAssignment, CapacityDemand
    from app.domains.inbox.models import InboxItem
    from tests.api.routes.test_m6_capacity_workload import user_id

    url, people, auth = capacity
    enable(client, capacity)
    person = people["E02"]
    start = this_week(db, person.org_id)
    endpoint = f"{url}/capacity"
    body = {
        "employee_id": str(person.id),
        "start_date": start.isoformat(),
        "end_date": (start + timedelta(days=1)).isoformat(),
    }
    for end in (start - timedelta(days=1), start + timedelta(days=92)):
        assert (
            client.post(
                f"{endpoint}/unavailability",
                headers=auth["employee"],
                json={**body, "end_date": end.isoformat()},
            ).status_code
            == 422
        )
    actor = user_id(db, people["E01"])
    demand = CapacityDemand(
        org_id=person.org_id,
        requesting_department_id=person.department_id,
        target_department_id=person.department_id,
        requester_user_id=actor,
        capability_key="synthetic",
        capability_name="合成能力",
        headcount=1,
        allocation_percent=60,
        window_start=start,
        window_end=start + timedelta(days=4),
        description="合成需求",
    )
    db.add(demand)
    db.flush()
    assignment = CapacityAssignment(
        org_id=person.org_id,
        demand_id=demand.id,
        employee_id=person.id,
        allocation_percent=60,
        start_date=start,
        due_date=start + timedelta(days=4),
        assigned_by_user_id=actor,
    )
    db.add(assignment)
    db.commit()
    db.refresh(assignment)
    before = assignment.model_dump()
    assert (
        client.put(
            f"{endpoint}/weeks",
            headers=auth["manager"],
            json={
                "start_week": start.isoformat(),
                "end_week": start.isoformat(),
                "items": [{"employee_id": str(person.id), "other_percent": 20}],
            },
        ).status_code
        == 200
    )
    result = client.post(
        f"{endpoint}/unavailability", headers=auth["manager"], json=body
    )
    assert result.status_code == 200 and result.json()["over_weeks"] == [
        start.isoformat()
    ]
    db.refresh(assignment)
    assert assignment.model_dump() == before
    assert db.exec(
        select(InboxItem).where(
            InboxItem.org_id == person.org_id,
            InboxItem.recipient_employee_id == person.id,
        )
    ).all()
    for event in db.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == person.org_id,
            AuditEvent.action.like("capacity.unavailability_%"),
        )
    ).all():
        assert "reason" not in event.details and event.details["employee_id"] == str(
            person.id
        )
    person.employment_status = "inactive"
    db.add(person)
    db.commit()
    assert (
        client.post(
            f"{endpoint}/unavailability", headers=auth["manager"], json=body
        ).status_code
        == 404
    )


def test_requester_cannot_read_unavailability(
    mcp_capacity: tuple, db: Session, client: TestClient
) -> None:
    import json

    from tests.api.routes.test_m6_capacity_mcp import listing, use
    from tests.api.routes.test_m6_capacity_workload import headers

    people, body, auth = mcp_capacity
    person = people["E04"]
    base = f"/api/v1/orgs/{person.org_id}/capacity"
    private = {
        "employee_id": str(person.id),
        "start_date": "2027-05-03",
        "end_date": "2027-05-04",
    }
    row = client.post(
        f"{base}/unavailability", headers=headers(db, person), json=private
    )
    assert row.status_code == 200, row.text
    requester = headers(db, people["E02"])
    params = {
        "employee_id": str(person.id),
        "from": private["start_date"],
        "to": private["end_date"],
    }
    assert (
        client.get(
            f"{base}/unavailability", headers=requester, params=params
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"{base}/unavailability", headers=requester, json=private
        ).status_code
        == 404
    )
    assert (
        client.delete(
            f"{base}/unavailability/{row.json()['id']}", headers=requester
        ).status_code
        == 404
    )
    created = use(client, auth["a"], "submit_demand", body)
    for name, args in [
        ("get_demand", {"demand_id": created["id"]}),
        ("list_demands", {}),
        ("get_my_workload", {}),
        (
            "capacity_reference",
            {k: body[k] for k in ("target_department_id", "capability_key")},
        ),
    ]:
        raw = json.dumps(use(client, auth["a"], name, args))
        assert (
            row.json()["id"] not in raw
            and private["start_date"] not in raw
            and "unavailability" not in raw
        )
    assert not any("unavailability" in name for name in listing(client, auth["a"]))
    db.exec(
        delete(CapacityUnavailability).where(
            CapacityUnavailability.org_id == person.org_id
        )
    )
    db.commit()
