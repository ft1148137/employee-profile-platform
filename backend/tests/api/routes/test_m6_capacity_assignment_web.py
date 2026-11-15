import json
from collections.abc import Generator
from datetime import date, timedelta
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlmodel import Session, select

from app.core.security import create_access_token
from app.domains.capacity.models import (
    CapacityAssignment,
    CapacityDemand,
    CapacityDemandEvent,
    CapacityExtensionRequest,
    CapacitySetting,
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


def test_demand_assignment_scope_and_inactive(case: tuple, db: Session) -> None:
    api, create, demand, people, _, _ = case
    second, first = create(code="w2"), create()
    path = f"/demands/{demand.id}/assignments"
    result = api(path)
    assert result["count"] == 2
    assert [r["employee_name"] for r in result["data"]] == sorted(
        [people["w"].display_name, people["w2"].display_name]
    )
    assert {r["id"] for r in result["data"]} == {first["id"], second["id"]}
    for role in ("a", "am", "cm", "w", "w2", "foreign"):
        api(path, role, status=404)
    api(path, "foreign", status=404, other_org=True)
    api(f"/demands/{uuid4()}/assignments", status=404)
    original_department = people["w"].department_id
    people["w"].department_id = people["a"].department_id
    db.add(people["w"])
    db.commit()
    assert [r["id"] for r in api(path)["data"]] == [second["id"]]
    people["w"].department_id = original_department
    people["w"].employment_status = "inactive"
    db.add(people["w"])
    db.commit()
    assert [r["id"] for r in api(path)["data"]] == [second["id"]]
    assert api(path, "admin")["count"] == 2
    setting = db.exec(
        select(CapacitySetting).where(CapacitySetting.org_id == demand.org_id)
    ).one()
    setting.enabled = False
    db.add(setting)
    db.commit()
    api(path, status=404)


@pytest.mark.parametrize("extension", [False, True])
def test_commitment_note_validation_privacy_and_context(
    case: tuple, db: Session, extension: bool
) -> None:
    api, create, demand, people, users, org = case
    row = create(code="w2")
    path = f"/assignments/{row['id']}"
    method = "PATCH"
    change = {"due_date": "2027-01-05"}
    if extension:
        pending = api(
            f"{path}/extension-requests",
            "w2",
            {"requested_due_date": "2027-01-05", "reason": "PRIVATE_REASON"},
        )
        context = {
            "employee_name": people["w2"].display_name,
            "demand_id": str(demand.id),
            "capability_name": demand.capability_name,
            "current_due_date": "2026-12-20",
            "committed_end_date": "2026-12-31",
        }
        assert {k: pending[k] for k in context} == context
        assert api("/extension-requests")["data"] == [pending]
        assert api("/extension-requests", "w2")["data"] == [pending]
        for role in ("a", "am"):
            assert api("/extension-requests", role) == {"data": [], "count": 0}
        path = f"/extension-requests/{pending['id']}/decision"
        method, change = "POST", {"approve": True, "note": "PRIVATE_NOTE"}
        for fields in (
            {"new_committed_end_date": "2027-01-10"},
            {"commitment_note": "部门说明"},
            {"new_committed_end_date": "2027-01-10", "commitment_note": "部门说明"},
        ):
            api(path, body={"approve": False, "note": "退回", **fields}, status=422)
    for fields in (
        {"new_committed_end_date": "2027-01-10"},
        {"commitment_note": "部门说明"},
        {"new_committed_end_date": "2027-01-10", "commitment_note": " "},
    ):
        api(path, body={**change, **fields}, status=422, method=method)
    for person in (people["bm"], people["cm"], people["w"], people["w2"]):
        for private in (person.display_name, person.employee_code, str(person.id)):
            api(
                path,
                body={
                    **change,
                    "new_committed_end_date": "2027-01-10",
                    "commitment_note": f"等待{private}完成",
                },
                status=422,
                method=method,
            )
    assert api(f"/demands/{demand.id}", "a")["committed_end_date"] == "2026-12-31"
    assert api(role="w2")["data"][0]["due_date"] == "2026-12-20"
    assert not db.exec(
        select(InboxItem).where(
            InboxItem.org_id == org.id, InboxItem.recipient_user_id == users["a"].id
        )
    ).all()
    note = "部门需要追加联调，调整交付日期"
    result = api(
        path,
        body={
            **change,
            "new_committed_end_date": "2027-01-10",
            "commitment_note": note,
        },
        method=method,
    )
    if extension:
        context.update(current_due_date="2027-01-05", committed_end_date="2027-01-10")
        assert {k: result[k] for k in context} == context
        assert api("/extension-requests")["data"] == [result]
    public = api(f"/demands/{demand.id}", "a")
    assert public["events"][0]["text"] == note
    assert public["events"][0]["kind"] == "commitment_changed"
    assert public["committed_end_date"] == "2027-01-10"
    for role in ("a", "am"):
        responses = [
            api(f"/demands/{demand.id}", role),
            api("/demands", role),
            api("/extension-requests", role),
            api(f"/demands/{demand.id}/assignments", role, status=404),
        ]
        raw = json.dumps(responses, ensure_ascii=False)
        for person in (people["bm"], people["cm"], people["w"], people["w2"]):
            assert all(
                v not in raw
                for v in (person.display_name, person.employee_code, str(person.id))
            )
        assert "PRIVATE_" not in raw
    recipients = db.exec(
        select(InboxItem.recipient_user_id).where(InboxItem.org_id == org.id)
    ).all()
    assert recipients.count(users["a"].id) == recipients.count(users["am"].id) == 1
    if not extension:
        api(
            path,
            body={"due_date": "2027-01-06", "commitment_note": note},
            status=422,
            method=method,
        )
        api(
            path,
            body={
                "due_date": "2027-01-06",
                "new_committed_end_date": "2027-01-10",
                "commitment_note": note,
            },
            status=409,
            method=method,
        )
