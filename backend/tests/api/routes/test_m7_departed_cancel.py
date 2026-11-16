from datetime import date, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlmodel import Session, select

from app.core.security import create_access_token
from app.domains.audit.models import AuditEvent
from app.domains.capacity.models import (
    CapacityAssignment,
    CapacityDemand,
    CapacityExtensionRequest,
    CapacitySetting,
)
from app.domains.org.models import (
    Department,
    Employee,
    Organization,
    OrgMembership,
    RoleGrant,
)
from app.models import User
from tests.utils.org import delete_test_organization


def test_departed_assignment_read_and_cancel(db: Session, client: TestClient) -> None:
    org, foreign = [Organization(slug=str(uuid4()), name="合成企业") for _ in range(2)]
    db.add_all([org, foreign])
    db.flush()
    a, b = [Department(org_id=org.id, name=n) for n in ("需求部门", "承接部门")]
    db.add_all([a, b])
    db.flush()
    child = Department(org_id=org.id, name="承接子部门", parent_department_id=b.id)
    db.add(child)
    db.flush()
    people: dict[str, Employee] = {}
    users: dict[str, User] = {}
    headers: dict[str, dict[str, str]] = {}
    for role, dept in {
        "a": a,
        "am": a,
        "bm": b,
        "cm": child,
        "worker": child,
        "admin": None,
        "foreign": None,
    }.items():
        user = User(
            username=str(uuid4()),
            email=f"{uuid4()}@example.com",
            hashed_password="unused",
        )
        db.add(user)
        db.flush()
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
            db.flush()
            people[role] = person
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
        headers[role] = {
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
        headcount=1,
        allocation_percent=20,
        window_start=date(2026, 12, 1),
        window_end=date(2026, 12, 31),
        committed_start_date=date(2026, 12, 1),
        committed_end_date=date(2026, 12, 31),
        status="accepted",
        description="合成工作说明",
    )
    db.add(demand)
    db.flush()
    row = CapacityAssignment(
        org_id=org.id,
        demand_id=demand.id,
        employee_id=people["worker"].id,
        allocation_percent=20,
        start_date=date(2026, 12, 1),
        due_date=date(2026, 12, 20),
        assigned_by_user_id=users["bm"].id,
    )
    db.add(row)
    db.flush()
    extension = CapacityExtensionRequest(
        org_id=org.id,
        assignment_id=row.id,
        requested_due_date=date(2026, 12, 25),
        reason="合成延期",
    )
    db.add(extension)
    people["worker"].employment_status = "inactive"
    db.add(people["worker"])
    db.commit()

    list_path = f"/api/v1/orgs/{org.id}/capacity/demands/{demand.id}/assignments"
    update_path = f"/api/v1/orgs/{org.id}/capacity/assignments/{row.id}"
    for role in ("a", "am", "cm", "worker", "foreign"):
        assert client.get(list_path, headers=headers[role]).status_code == 404
        assert (
            client.patch(
                update_path,
                headers=headers[role],
                json={"status": "cancelled"},
            ).status_code
            == 404
        )
    foreign_list = list_path.replace(str(org.id), str(foreign.id))
    assert client.get(foreign_list, headers=headers["foreign"]).status_code == 404

    listed = client.get(list_path, headers=headers["bm"])
    assert listed.status_code == 200
    assert listed.json() == {
        "data": [
            {
                "id": str(row.id),
                "allocation_percent": 20,
                "start_date": "2026-12-01",
                "due_date": "2026-12-20",
                "employee_name": "已离职员工",
                "employee_departed": True,
            }
        ],
        "count": 1,
    }
    assert people["worker"].display_name not in listed.text
    assert people["worker"].employee_code not in listed.text
    assert str(people["worker"].id) not in listed.text
    admin_row = client.get(list_path, headers=headers["admin"]).json()["data"][0]
    assert admin_row["employee_name"] == people["worker"].display_name
    assert admin_row["employee_id"] == str(people["worker"].id)
    assert admin_row["employee_departed"] is True

    for body in (
        {"start_date": "2026-12-02"},
        {"due_date": "2026-12-21"},
        {"status": "done"},
    ):
        assert (
            client.patch(update_path, headers=headers["bm"], json=body).status_code
            == 404
        )
    assert (
        client.post(
            f"/api/v1/orgs/{org.id}/capacity/extension-requests/{extension.id}/decision",
            headers=headers["cm"],
            json={"approve": True},
        ).status_code
        == 404
    )
    assert (
        client.patch(
            update_path, headers=headers["admin"], json={"status": "cancelled"}
        ).status_code
        == 404
    )
    db.refresh(row)
    assert row.status == "active"
    cancelled = client.patch(
        update_path, headers=headers["bm"], json={"status": "cancelled"}
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json() == {"id": str(row.id), "status": "cancelled"}
    assert people["worker"].display_name not in cancelled.text
    assert people["worker"].employee_code not in cancelled.text
    assert str(people["worker"].id) not in cancelled.text
    db.refresh(row)
    assert row.status == "cancelled"
    assert client.get(list_path, headers=headers["bm"]).json() == {
        "data": [],
        "count": 0,
    }
    events = db.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == org.id,
            AuditEvent.action == "capacity.assignment_updated",
            AuditEvent.target_id == str(row.id),
        )
    ).all()
    assert len(events) == 1
    assert events[0].actor_id == str(users["bm"].id)
    assert events[0].details["status"] == "cancelled"

    db.rollback()
    for model in (
        CapacityExtensionRequest,
        CapacityAssignment,
        CapacityDemand,
        CapacitySetting,
    ):
        db.execute(delete(model).where(model.org_id == org.id))
    db.execute(delete(CapacitySetting).where(CapacitySetting.org_id == foreign.id))
    db.commit()
    for tenant in (org, foreign):
        delete_test_organization(db, tenant.id)
