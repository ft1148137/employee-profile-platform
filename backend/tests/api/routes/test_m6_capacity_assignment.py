import json
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlmodel import Session, select

from app.authz.deps import get_org_principal
from app.core.security import create_access_token
from app.domains.audit.models import AuditEvent
from app.domains.capacity import assignment
from app.domains.capacity.assignment_schemas import AssignmentUpdateInput
from app.domains.capacity.models import (
    CapacityAssignment,
    CapacityDemand,
    CapacityDemandEvent,
    CapacitySetting,
    CapacityWorkload,
)
from app.domains.inbox.models import InboxItem
from app.domains.org.models import (
    Department,
    Employee,
    Organization,
    OrgMembership,
    RoleGrant,
)
from app.models import User
from tests.utils.org import delete_test_organization


@pytest.fixture
def case(db: Session, client: TestClient) -> Generator[tuple]:
    org, foreign = [Organization(slug=str(uuid4()), name="合成企业") for _ in range(2)]
    db.add_all([org, foreign])
    db.flush()
    a, b = [Department(org_id=org.id, name=n) for n in ("需求部门", "承接部门")]
    db.add_all([a, b])
    db.flush()
    child = Department(org_id=org.id, name="合成下级", parent_department_id=b.id)
    db.add(child)
    db.flush()
    people, users, auth = {}, {}, {}
    for role, dept in {
        "a": a,
        "am": a,
        "bm": b,
        "cm": child,
        "w": child,
        "w2": b,
        "inactive": b,
        "admin": None,
        "foreign": None,
    }.items():
        user = User(
            username=str(uuid4()),
            email=f"{uuid4()}@example.com",
            hashed_password="unused",
        )
        db.add(user)
        person = (
            Employee(
                org_id=org.id,
                department_id=dept.id,
                display_name=f"合成员工{role}",
                employee_code=f"PRIVATE_{role}",
                employment_status="inactive" if role == "inactive" else "active",
            )
            if dept
            else None
        )
        if person:
            db.add(person)
            people[role] = person
        db.flush()
        member = OrgMembership(
            org_id=foreign.id if role == "foreign" else org.id,
            user_id=user.id,
            employee_id=person.id if person else None,
        )
        db.add(member)
        db.flush()
        if role in ("am", "bm", "cm", "admin"):
            db.add(
                RoleGrant(
                    org_id=org.id,
                    membership_id=member.id,
                    role="org_admin" if role == "admin" else "dept_manager",
                    department_id=dept.id if dept else None,
                )
            )
        users[role] = user
        auth[role] = {
            "Authorization": f"Bearer {create_access_token(user.id, expires_delta=timedelta(minutes=30))}"
        }
    db.add(
        CapacitySetting(
            org_id=org.id, enabled=True, updated_by_user_id=users["admin"].id
        )
    )
    db.add(
        CapacitySetting(
            org_id=foreign.id, enabled=True, updated_by_user_id=users["foreign"].id
        )
    )
    db.add(
        CapacityWorkload(
            org_id=org.id,
            employee_id=people["w"].id,
            base_percent=70,
            updated_by_user_id=users["bm"].id,
        )
    )
    demand = CapacityDemand(
        org_id=org.id,
        requesting_department_id=a.id,
        target_department_id=b.id,
        requester_user_id=users["a"].id,
        capability_key="synthetic",
        capability_name="合成能力",
        headcount=2,
        allocation_percent=50,
        window_start=date(2026, 12, 1),
        window_end=date(2026, 12, 31),
        committed_start_date=date(2026, 12, 1),
        committed_end_date=date(2026, 12, 31),
        status="accepted",
        description="合成工作说明",
    )
    db.add(demand)
    db.commit()
    org_id, foreign_id, demand_id = org.id, foreign.id, demand.id
    employee_ids = {code: person.id for code, person in people.items()}

    def api(
        path: str = "/assignments/me",
        role: str = "bm",
        body: dict | None = None,
        status: int | None = 200,
        method: str | None = None,
        other_org: bool = False,
    ) -> Any:
        response = client.request(
            method or ("GET" if body is None else "POST"),
            f"/api/v1/orgs/{foreign_id if other_org else org_id}/capacity{path}",
            headers=auth[role],
            json=body,
        )
        if status is None:
            return response
        assert response.status_code == status, response.text
        return response.json()

    def create(
        role: str = "bm",
        code: str = "w",
        percent: int = 20,
        status: int | None = 200,
        due: str = "2026-12-20",
    ) -> Any:
        return api(
            f"/demands/{demand_id}/assignments",
            role,
            {
                "employee_id": str(employee_ids[code]),
                "allocation_percent": percent,
                "due_date": due,
            },
            status,
        )

    yield api, create, demand, people, users, org
    db.rollback()
    for model in (
        CapacityAssignment,
        CapacityDemandEvent,
        CapacityDemand,
        CapacityWorkload,
        CapacitySetting,
    ):
        db.execute(delete(model).where(model.org_id == org.id))
    db.commit()
    db.execute(delete(CapacitySetting).where(CapacitySetting.org_id == foreign.id))
    db.commit()
    for tenant in (org, foreign):
        delete_test_organization(db, tenant.id)


@pytest.mark.parametrize(
    ("percent", "band", "status"),
    [(20, "full", "done"), (40, "overloaded", "cancelled")],
)
def test_assignment_load_self_read_and_close(
    case: tuple, db: Session, percent: int, band: str, status: str
) -> None:
    api, create, demand, people, _, org = case
    row = create(percent=percent)
    create(status=409)
    create(code="w2")
    own = api(role="w")
    assert own["count"] == 1 and own["data"][0] == {
        **row,
        "requesting_department_name": "需求部门",
        "capability_name": "合成能力",
        "description": "合成工作说明",
    }
    load = api("/workloads/me", "w")
    assert (load["platform_percent"], load["confirmed_percent"], load["band"]) == (
        percent,
        70 + percent,
        band,
    )
    assert api("/workloads/me", "w2")["band"] == "unconfirmed"
    closed = api(f"/assignments/{row['id']}", body={"status": status}, method="PATCH")
    assert closed["closed_at"] and closed["status"] == status
    load = api("/workloads/me", "w")
    assert (load["platform_percent"], load["confirmed_percent"], load["band"]) == (
        0,
        70,
        "available",
    )
    api(
        f"/assignments/{row['id']}",
        body={"due_date": "2026-12-21"},
        method="PATCH",
        status=409,
    )
    for role in ("a", "am", "bm", "cm"):
        assert api(role=role) == {"data": [], "count": 0}
    raw = json.dumps(
        [api(f"/demands/{demand.id}", "a"), api("/demands", "am")], ensure_ascii=False
    )
    for person in (people["w"], people["w2"]):
        assert all(
            private not in raw
            for private in (str(person.id), person.display_name, person.employee_code)
        )
    items = db.exec(select(InboxItem).where(InboxItem.org_id == org.id)).all()
    assert {i.recipient_employee_id for i in items} == {people["w"].id, people["w2"].id}


def test_scope_status_and_input_validation(case: tuple, db: Session) -> None:
    api, create, demand, people, _, _ = case
    for state in ("submitted", "replied", "withdrawn"):
        demand.status = state
        db.add(demand)
        db.commit()
        create(status=409)
    demand.status = "accepted"
    db.add(demand)
    db.commit()
    for role in ("a", "am", "cm", "w", "foreign"):
        create(role=role, status=404)
    for code in ("a", "inactive"):
        create(code=code, status=404)
    for percent in (9, 101, True):
        create(percent=percent, status=422)
    create(due="2027-01-01", status=409)
    row = create()
    for role in ("a", "am", "w", "foreign", "admin"):
        api(f"/assignments/{row['id']}", role, {"due_date": "2026-12-21"}, 404, "PATCH")
    for body in (
        {},
        {"status": "active"},
        {"due_date": None},
        {"status": "done", "due_date": "2026-12-21"},
        {"status": "done", "new_committed_end_date": "2027-01-01"},
        {"employee_id": str(people["a"].id)},
    ):
        api(f"/assignments/{row['id']}", body=body, status=422, method="PATCH")
    api(f"/assignments/{uuid4()}", body={"status": "done"}, status=404, method="PATCH")
    api(role="foreign", status=404)
    api(
        f"/assignments/{row['id']}",
        "foreign",
        {"status": "done"},
        404,
        "PATCH",
        other_org=True,
    )
    api(
        f"/demands/{demand.id}/assignments",
        "foreign",
        {
            "employee_id": str(people["w"].id),
            "allocation_percent": 20,
            "due_date": "2026-12-20",
        },
        404,
        other_org=True,
    )
    people["w"].employment_status = "inactive"
    db.add(people["w"])
    db.commit()
    api(
        f"/assignments/{row['id']}", body={"status": "done"}, status=404, method="PATCH"
    )
    api(role="w", status=404)


def test_commitment_atomicity_notifications_timeline_and_audit(
    case: tuple, db: Session
) -> None:
    api, create, demand, people, users, org = case
    row, other = create(), create(code="w2")
    path = f"/assignments/{row['id']}"
    api(path, "cm", {"due_date": "2026-12-25"}, method="PATCH")
    assert api(f"/demands/{demand.id}", "a")["revision"] == 1
    assert (
        db.exec(
            select(InboxItem).where(
                InboxItem.org_id == org.id, InboxItem.recipient_user_id == users["a"].id
            )
        ).all()
        == []
    )
    for role, body in [
        ("bm", {}),
        ("cm", {"new_committed_end_date": "2027-01-10"}),
        ("bm", {"new_committed_end_date": "2027-01-04"}),
    ]:
        result = api(path, role, {"due_date": "2027-01-05", **body}, 409, "PATCH")
        assert (
            result["detail"]
            == "会推迟对 A 部门的承诺，请 B 部门经理同时确认新的承诺日期"
        )
    # A legacy active assignment may already exceed the current commitment.
    legacy = db.get_one(CapacityAssignment, UUID(other["id"]))
    legacy.due_date = date(2027, 1, 20)
    db.add(legacy)
    db.commit()
    api(
        path,
        body={"due_date": "2027-01-05", "new_committed_end_date": "2027-01-10"},
        status=409,
        method="PATCH",
    )
    before = api(f"/demands/{demand.id}", "a")
    assert before["committed_end_date"] == "2026-12-31" and before["events"] == []
    assert api(role="w")["data"][0]["due_date"] == "2026-12-25"
    api(
        path,
        body={"due_date": "2027-01-05", "new_committed_end_date": "2027-01-20"},
        method="PATCH",
    )
    public = api(f"/demands/{demand.id}", "a")
    event = public["events"][-1]
    assert (public["revision"], public["committed_end_date"]) == (2, "2027-01-20")
    assert (
        event["kind"],
        event["previous_end_date"],
        event["end_date"],
        event["revision"],
    ) == ("commitment_changed", "2026-12-31", "2027-01-20", 2)
    assert event["text"] and event["basis"] is None and event["responder_name"] is None
    raw = json.dumps(public, ensure_ascii=False)
    for person in people.values():
        if person.department_id != demand.requesting_department_id:
            assert all(
                private not in raw
                for private in (
                    str(person.id),
                    person.display_name,
                    person.employee_code,
                )
            )
    recipients = db.exec(
        select(InboxItem.recipient_user_id).where(
            InboxItem.org_id == org.id, InboxItem.recipient_user_id.is_not(None)
        )
    ).all()
    assert sorted(recipients) == sorted([users["a"].id, users["am"].id])
    events = db.exec(
        select(AuditEvent)
        .where(AuditEvent.org_id == org.id)
        .order_by(AuditEvent.created_at)
    ).all()
    assert [e.action for e in events] == [
        "capacity.assignment_created",
        "capacity.assignment_created",
        "capacity.assignment_updated",
        "capacity.commitment_changed",
        "capacity.assignment_updated",
    ]
    assert all(
        e.details["via"] == "web"
        and "client_name" in e.details
        and "合成工作说明" not in str(e.details)
        for e in events
    )


def test_service_provenance_and_disabled_module(case: tuple, db: Session) -> None:
    api, create, demand, _, users, org = case
    row = create(role="admin")
    with Session(db.get_bind()) as session:
        principal = get_org_principal(org.id, users["bm"], session)
        assignment.update_assignment(
            principal,
            session,
            UUID(row["id"]),
            AssignmentUpdateInput(
                due_date="2027-01-01", new_committed_end_date="2027-01-02"
            ),
            via="mcp",
            client_name="synthetic-client",
        )
        session.commit()
    events = db.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == org.id,
            AuditEvent.action != "capacity.assignment_created",
        )
    ).all()
    assert len(events) == 2 and all(
        e.details["via"] == "mcp" and e.details["client_name"] == "synthetic-client"
        for e in events
    )
    api("/assignments/me", "admin", status=404)
    setting = db.exec(
        select(CapacitySetting).where(CapacitySetting.org_id == org.id)
    ).one()
    setting.enabled = False
    db.add(setting)
    db.commit()
    create(status=404)
    api(role="w", status=404)
    api(
        f"/assignments/{row['id']}", body={"status": "done"}, method="PATCH", status=404
    )


def test_concurrent_assignment_and_commitment_writes(case: tuple) -> None:
    api, create, demand, _, _, _ = case

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(
            pool.map(lambda _: create(status=None).status_code, range(2))
        ) == [200, 409]
    row = api(role="w")["data"][0]

    def adjust(_: int) -> int:
        return api(
            f"/assignments/{row['id']}",
            body={"due_date": "2027-01-01", "new_committed_end_date": "2027-01-01"},
            method="PATCH",
            status=None,
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(adjust, range(2))) == [200, 409]
    result = api(f"/demands/{demand.id}", "a")
    assert result["revision"] == 2 and len(result["events"]) == 1
