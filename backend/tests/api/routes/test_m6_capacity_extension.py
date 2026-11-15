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
from app.domains.base import utc_now
from app.domains.capacity import extension
from app.domains.capacity.extension_schemas import ExtensionDecisionInput
from app.domains.capacity.models import (
    CapacityAssignment,
    CapacityDemand,
    CapacityDemandEvent,
    CapacityExtensionRequest,
    CapacitySetting,
)
from app.domains.capacity.settings import CapacityNotFound
from app.domains.capacity.tools import CAPACITY_TOOLS
from app.domains.inbox.models import InboxItem
from app.domains.org.models import (
    Department,
    Employee,
    Organization,
    OrgMembership,
    RoleGrant,
)
from app.domains.review.models import ReviewDelegation
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
    for tenant, role in ((org, "admin"), (foreign, "foreign")):
        db.add(
            CapacitySetting(
                org_id=tenant.id, enabled=True, updated_by_user_id=users[role].id
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

    def create(code: str = "w") -> dict:
        return api(
            f"/demands/{demand_id}/assignments",
            body={
                "employee_id": str(employee_ids[code]),
                "allocation_percent": 20,
                "due_date": "2026-12-20",
            },
        )

    yield api, create, demand, people, users, org
    db.rollback()
    for model in (
        CapacityExtensionRequest,
        CapacityAssignment,
        CapacityDemandEvent,
        CapacityDemand,
        CapacitySetting,
    ):
        db.execute(delete(model).where(model.org_id == org.id))
    db.commit()
    db.execute(delete(CapacitySetting).where(CapacitySetting.org_id == foreign.id))
    db.commit()
    for tenant in (org, foreign):
        delete_test_organization(db, tenant.id)


def request(
    api: Any, row: dict, due: str = "2026-12-25", role: str = "w", status: int = 200
) -> dict:
    return api(
        f"/assignments/{row['id']}/extension-requests",
        role,
        {"requested_due_date": due, "reason": "PRIVATE_REASON"},
        status,
    )


def decide(
    api: Any, pending: dict, role: str = "cm", status: int = 200, **body: Any
) -> dict:
    return api(
        f"/extension-requests/{pending['id']}/decision",
        role,
        {"approve": True, **body},
        status,
    )


def test_own_request_permissions_validation_and_lifecycle(
    case: tuple, db: Session
) -> None:
    api, create, _, people, _, org = case
    row = create()
    for role in ("w2", "a", "am", "bm", "cm", "admin", "foreign"):
        request(api, row, role=role, status=404)
    for due in ("2026-12-19", "2026-12-20"):
        request(api, row, due, status=409)
    for body in (
        {"requested_due_date": "2026-12-25", "reason": " "},
        {"requested_due_date": "invalid", "reason": "合成"},
        {
            "requested_due_date": "2026-12-25",
            "reason": "合成",
            "employee_id": str(people["w2"].id),
        },
    ):
        api(f"/assignments/{row['id']}/extension-requests", "w", body, 422)
    pending = request(api, row)
    request(api, row, status=409)
    for role in ("w", "w2", "a", "am", "bm", "admin", "foreign"):
        decide(api, pending, role, 404)
    listed = {**pending, "reviewer_names": []}
    assert api("/extension-requests", "w")["data"] == [listed]
    assert api("/extension-requests", "cm")["data"] == [listed]
    for role in ("w2", "a", "am", "bm", "admin"):
        assert api("/extension-requests", role) == {"data": [], "count": 0}
    api("/extension-requests", "foreign", status=404)
    api(
        f"/extension-requests/{pending['id']}/decision",
        "foreign",
        {"approve": True},
        404,
        other_org=True,
    )
    api(
        f"/assignments/{row['id']}/extension-requests",
        "foreign",
        {"requested_due_date": "2026-12-25", "reason": "合成"},
        404,
        other_org=True,
    )
    decide(api, pending, status=422, approve=False)
    decide(api, pending, status=422, approve=False, note=" ")
    returned = decide(api, pending, approve=False, note="PRIVATE_NOTE")
    assert (
        returned["status"] == "returned" and returned["decision_note"] == "PRIVATE_NOTE"
    )
    assert api(role="w")["data"][0]["due_date"] == "2026-12-20"
    decide(api, pending, status=409)
    assert api("/extension-requests", "cm")["data"] == [returned]
    pending = request(api, row)
    approved = decide(api, pending)
    assert approved["status"] == "approved" and approved["decided_at"]
    assert api(role="w")["data"][0]["due_date"] == "2026-12-25"
    notices = db.exec(select(InboxItem).where(InboxItem.org_id == org.id)).all()
    assert any(
        i.recipient_employee_id == people["w"].id and "PRIVATE_NOTE" in i.message
        for i in notices
    )
    assert len([i for i in notices if i.recipient_employee_id == people["w"].id]) == 3
    assert all(
        i.recipient_user_id is None or i.recipient_user_id != case[4]["a"].id
        for i in notices
    )
    events = db.exec(
        select(AuditEvent)
        .where(AuditEvent.org_id == org.id)
        .order_by(AuditEvent.created_at)
    ).all()
    assert [e.action for e in events] == [
        "capacity.assignment_created",
        "capacity.extension_requested",
        "capacity.extension_returned",
        "capacity.extension_requested",
        "capacity.extension_approved",
    ]
    assert all(
        "PRIVATE_" not in str(e.details)
        and e.details["via"] == "web"
        and "client_name" in e.details
        for e in events
    )


def test_commitment_confirmation_atomicity_and_a_privacy(
    case: tuple, db: Session
) -> None:
    api, create, demand, people, users, org = case
    row, other = create(), create(code="w2")
    pending = request(api, row, "2027-01-05")
    # The child manager reviews w; the B manager can act as their valid delegate.
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
    for role, body in (
        (
            "cm",
            {
                "new_committed_end_date": "2027-01-10",
                "commitment_note": "部门调整交付安排",
            },
        ),
        ("bm", {}),
        (
            "bm",
            {
                "new_committed_end_date": "2027-01-04",
                "commitment_note": "部门调整交付安排",
            },
        ),
    ):
        result = decide(api, pending, role, 409, **body)
        assert result["detail"] == (
            "会推迟对需求方的承诺：请同时填写新的承诺日期（不早于本需求各分配的完成日期）和对外说明"
            if role == "bm"
            else "会推迟对需求方的承诺，你不能确认新的承诺日期：请把审批代理给目标部门经理，或退回让员工改到承诺日期之内"
        )
    legacy = db.get_one(CapacityAssignment, UUID(other["id"]))
    legacy.due_date = date(2027, 1, 20)
    db.add(legacy)
    db.commit()
    decide(
        api,
        pending,
        "bm",
        409,
        new_committed_end_date="2027-01-10",
        commitment_note="部门调整交付安排",
    )
    before = api(f"/demands/{demand.id}", "a")
    assert (
        before["revision"] == 1
        and before["events"] == []
        and before["committed_end_date"] == "2026-12-31"
    )
    assert api(role="w")["data"][0]["due_date"] == "2026-12-20"
    assert api("/extension-requests", "w")["data"][0]["status"] == "pending"
    assert (
        db.exec(
            select(InboxItem).where(
                InboxItem.org_id == org.id, InboxItem.recipient_user_id == users["a"].id
            )
        ).all()
        == []
    )
    decide(
        api,
        pending,
        "bm",
        new_committed_end_date="2027-01-20",
        commitment_note="部门调整交付安排",
    )
    public = api(f"/demands/{demand.id}", "a")
    assert (public["revision"], public["committed_end_date"]) == (2, "2027-01-20")
    assert public["events"][0]["kind"] == "commitment_changed"
    raw = json.dumps(
        [public, api("/demands", "am"), api("/extension-requests", "a")],
        ensure_ascii=False,
    )
    for person in (people["bm"], people["cm"], people["w"], people["w2"]):
        assert all(
            private not in raw
            for private in (str(person.id), person.display_name, person.employee_code)
        )
    recipients = db.exec(
        select(InboxItem.recipient_user_id).where(InboxItem.org_id == org.id)
    ).all()
    assert (
        recipients.count(users["a"].id) == 1 and recipients.count(users["am"].id) == 1
    )
    actions = db.exec(
        select(AuditEvent.action)
        .where(AuditEvent.org_id == org.id)
        .order_by(AuditEvent.created_at)
    ).all()
    assert actions[-2:] == [
        "capacity.commitment_changed",
        "capacity.extension_approved",
    ]


def test_tools_delegation_expiry_and_provenance(case: tuple, db: Session) -> None:
    api, create, _, _, users, org = case
    row = create()
    create(code="w2")
    tools = {t.name: t for t in CAPACITY_TOOLS}
    delegation = ReviewDelegation(
        org_id=org.id,
        manager_user_id=users["cm"].id,
        delegate_user_id=users["w2"].id,
        created_by_user_id=users["cm"].id,
        starts_at=utc_now() - timedelta(hours=1),
        ends_at=utc_now() + timedelta(hours=1),
    )
    db.add(delegation)
    db.commit()
    with Session(db.get_bind()) as session:
        principal = get_org_principal(org.id, users["w"], session)
        own = tools["list_my_assignments"].handler(principal, session)
        assert (
            own.model_dump(mode="json")["data"][0]["id"] == row["id"] and own.count == 1
        )
        pending = tools["request_extension"].handler(
            principal,
            session,
            **tools["request_extension"]
            .input_model(
                assignment_id=row["id"],
                requested_due_date="2026-12-25",
                reason="PRIVATE_REASON",
            )
            .model_dump(),
            via="mcp",
            client_name="synthetic-client",
        )
        session.commit()
    assert set(
        db.exec(
            select(InboxItem.recipient_user_id).where(
                InboxItem.org_id == org.id, InboxItem.recipient_user_id.is_not(None)
            )
        ).all()
    ) == {users["cm"].id, users["w2"].id}
    pending = pending.model_dump(mode="json")
    assert api("/extension-requests", "w2")["data"] == [
        {**pending, "reviewer_names": [], "on_behalf_of_name": "合成员工cm"}
    ]
    delegation.ends_at = utc_now() - timedelta(minutes=1)
    db.add(delegation)
    db.commit()
    decide(api, pending, "w2", 404)
    assert api("/extension-requests", "w2")["count"] == 0
    delegation.ends_at = utc_now() + timedelta(hours=1)
    db.add(delegation)
    db.commit()
    with Session(db.get_bind()) as session:
        principal = get_org_principal(org.id, users["w2"], session)
        decided = extension.decide_extension_request(
            principal,
            session,
            UUID(pending["id"]),
            ExtensionDecisionInput(approve=True),
            via="bot",
            client_name="synthetic-bot",
        )
        assert (
            decided.decided_by_user_id == users["w2"].id
            and decided.decided_for_user_id == users["cm"].id
        )
        session.commit()
    assert (
        api("/extension-requests", "cm")["count"] == 1
        and api("/extension-requests", "w2")["count"] == 1
    )
    with Session(db.get_bind()) as session:
        principal = get_org_principal(org.id, users["w2"], session)
        with pytest.raises(CapacityNotFound):
            tools["request_extension"].handler(
                principal,
                session,
                assignment_id=UUID(row["id"]),
                requested_due_date=date(2026, 12, 28),
                reason="合成",
            )
    events = db.exec(
        select(AuditEvent)
        .where(
            AuditEvent.org_id == org.id,
            AuditEvent.action.in_(
                ["capacity.extension_requested", "capacity.extension_approved"]
            ),
        )
        .order_by(AuditEvent.created_at)
    ).all()
    assert [(e.details["via"], e.details["client_name"]) for e in events] == [
        ("mcp", "synthetic-client"),
        ("bot", "synthetic-bot"),
    ]


def test_inactive_closed_stale_and_disabled(case: tuple, db: Session) -> None:
    api, create, _, people, _, org = case
    row = create()
    pending = request(api, row)
    api(f"/assignments/{row['id']}", body={"due_date": "2026-12-28"}, method="PATCH")
    decide(api, pending, status=409)
    decide(api, pending, approve=False, note="日期已由经理调整")
    pending = request(api, row, "2026-12-30")
    api(f"/assignments/{row['id']}", body={"status": "done"}, method="PATCH")
    request(api, row, "2026-12-31", status=409)
    decide(api, pending, status=409)
    people["w"].employment_status = "inactive"
    db.add(people["w"])
    db.commit()
    request(api, row, "2026-12-31", status=404)
    decide(api, pending, status=404)
    assert api("/extension-requests", "cm")["count"] == 0
    setting = db.exec(
        select(CapacitySetting).where(CapacitySetting.org_id == org.id)
    ).one()
    setting.enabled = False
    db.add(setting)
    db.commit()
    api("/extension-requests", "cm", status=404)
    decide(api, pending, status=404)
    request(api, row, "2026-12-31", status=404)


def test_concurrent_requests_and_decisions(case: tuple) -> None:
    api, create, _, _, _, _ = case
    row = create()

    def submit(_: int) -> int:
        return api(
            f"/assignments/{row['id']}/extension-requests",
            "w",
            {"requested_due_date": "2026-12-25", "reason": "合成并发"},
            None,
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(submit, range(2))) == [200, 409]
    pending = api("/extension-requests", "w")["data"][0]

    def approve(_: int) -> int:
        return api(
            f"/extension-requests/{pending['id']}/decision",
            "cm",
            {"approve": True},
            None,
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(approve, range(2))) == [200, 409]
