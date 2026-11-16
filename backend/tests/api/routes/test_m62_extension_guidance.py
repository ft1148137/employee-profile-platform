import json
from collections.abc import Generator
from datetime import date, timedelta
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlmodel import Session, select

from app.authz.deps import get_org_principal
from app.core.security import create_access_token
from app.domains.base import utc_now
from app.domains.capacity import assistant
from app.domains.capacity.models import (
    CapacityAssignment,
    CapacityDemand,
    CapacityDemandEvent,
    CapacityExtensionRequest,
    CapacitySetting,
    CapacityWeek,
    CapacityWorkloadRequest,
)
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
from tests.api.routes.test_m6_capacity_extension import decide, request
from tests.api.routes.test_mcp import call, initialize, token_for
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
        CapacityWorkloadRequest,
        CapacityWeek,
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


def test_blocked_guidance_and_delegating_to_commitment_confirmer(
    case: tuple, client: TestClient
) -> None:
    api, create, demand, people, users, org = case
    row = create()
    pending = request(api, row, "2027-01-05")
    manager = api("/extension-requests", "cm")["data"][0]
    assert manager["commitment_confirmer_names"] == [people["bm"].display_name]
    assert manager["on_behalf_of_name"] is None
    assert (
        api("/extension-requests", "w")["data"][0]["commitment_confirmer_names"] == []
    )
    headers = {
        "Authorization": f"Bearer {create_access_token(users['cm'].id, expires_delta=timedelta(minutes=30))}"
    }
    delegation = client.post(
        f"/api/v1/orgs/{org.id}/me/review-delegations",
        headers=headers,
        json={
            "delegate_user_id": str(users["bm"].id),
            "starts_at": (utc_now() - timedelta(minutes=1)).isoformat(),
            "ends_at": (utc_now() + timedelta(hours=1)).isoformat(),
        },
    )
    assert delegation.status_code == 201, delegation.text
    delegated = api("/extension-requests", "bm")["data"][0]
    assert delegated["on_behalf_of_name"] == people["cm"].display_name
    assert delegated["commitment_confirmer_names"] == []
    decide(
        api,
        pending,
        "bm",
        new_committed_end_date="2027-01-05",
        commitment_note="调整部门交付日期",
    )
    assert api(f"/demands/{demand.id}", "a")["committed_end_date"] == "2027-01-05"
    own_review = request(api, create("w2"), "2027-01-10", role="w2")
    target_card = next(
        r
        for r in api("/extension-requests", "bm")["data"]
        if r["id"] == own_review["id"]
    )
    assert target_card["commitment_confirmer_names"] == []
    assert target_card["on_behalf_of_name"] is None


def test_reviewer_names_match_notified_users_and_delegated_cards(
    case: tuple, db: Session
) -> None:
    api, create, _, people, users, org = case
    row = create()
    db.add(
        ReviewDelegation(
            org_id=org.id,
            manager_user_id=users["cm"].id,
            delegate_user_id=users["w2"].id,
            created_by_user_id=users["cm"].id,
            starts_at=utc_now() - timedelta(minutes=1),
            ends_at=utc_now() + timedelta(hours=1),
        )
    )
    db.commit()
    names = {people["cm"].display_name, people["w2"].display_name}
    assert set(api(role="w")["data"][0]["reviewer_names"]) == names
    pending = request(api, row, "2027-01-05")
    assert set(pending["reviewer_names"]) == names
    recipients = set(
        db.exec(
            select(InboxItem.recipient_user_id).where(
                InboxItem.org_id == org.id, InboxItem.message == "有延期申请等你审批"
            )
        ).all()
    )
    assert recipients == {users["cm"].id, users["w2"].id}
    card = api("/extension-requests", "w2")["data"][0]
    assert card["on_behalf_of_name"] == people["cm"].display_name
    assert card["commitment_confirmer_names"] == [people["bm"].display_name]
    api("/workloads/me/requests", "w", {"requested_percent": 40, "reason": "合成调整"})
    assert (
        api("/workload-requests", "w2")["data"][0]["on_behalf_of_name"]
        == people["cm"].display_name
    )
    assert api("/workload-requests", "cm")["data"][0]["on_behalf_of_name"] is None


def test_no_guidance_within_commitment_and_no_available_reviewers(
    case: tuple, db: Session
) -> None:
    api, create, _, _, users, _ = case
    row = create()
    request(api, row)
    assert (
        api("/extension-requests", "cm")["data"][0]["commitment_confirmer_names"] == []
    )
    users["cm"].is_active = False
    db.add(users["cm"])
    users["bm"].is_active = False
    db.add(users["bm"])
    users["admin"].is_active = False
    db.add(users["admin"])
    db.commit()
    assert api(role="w")["data"][0]["reviewer_names"] == []


def test_requesting_side_and_foreign_tenant_cannot_read_private_names(
    case: tuple, db: Session, client: TestClient
) -> None:
    api, create, demand, people, users, org = case
    row = create()
    db.add(
        ReviewDelegation(
            org_id=org.id,
            manager_user_id=users["cm"].id,
            delegate_user_id=users["w2"].id,
            created_by_user_id=users["cm"].id,
            starts_at=utc_now() - timedelta(minutes=1),
            ends_at=utc_now() + timedelta(hours=1),
        )
    )
    db.commit()
    request(api, row, "2027-01-05")
    for role in ("a", "am"):
        headers = initialize(client, token_for(db, people[role])[0])
        principal = get_org_principal(org.id, users[role], db)
        for name, args, path in (
            ("get_demand", {"demand_id": str(demand.id)}, f"/demands/{demand.id}"),
            ("list_demands", {"side": "outgoing"}, "/demands?side=outgoing"),
        ):
            mcp = call(client, headers, name, args)
            assert not mcp.get("isError"), mcp
            results = [
                api(path, role),
                mcp,
                assistant.call(db, principal, [], name, args),
            ]
            raw = json.dumps(results, ensure_ascii=False)
            for code in ("bm", "w", "cm", "w2"):
                assert people[code].display_name not in raw
                assert people[code].employee_code not in raw
                assert str(people[code].id) not in raw
        assert api("/extension-requests", role)["data"] == []
    api("/extension-requests", "foreign", status=404)
    api(f"/demands/{demand.id}", "foreign", status=404)
    assert api("/extension-requests", "foreign", other_org=True)["data"] == []
