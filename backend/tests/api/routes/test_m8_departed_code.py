from datetime import date, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlmodel import Session

from app.core.security import create_access_token
from app.domains.capacity.models import CapacityDemand, CapacitySetting
from app.domains.org.models import (
    Department,
    Employee,
    Organization,
    OrgMembership,
    RoleGrant,
)
from app.models import User
from tests.utils.org import delete_test_organization


def test_departed_assignment_code_respects_target_manager_scope(
    db: Session, client: TestClient
) -> None:
    org = Organization(slug=str(uuid4()), name="合成企业")
    db.add(org)
    db.flush()
    requester = Department(org_id=org.id, name="需求部门")
    target = Department(org_id=org.id, name="承接部门")
    db.add_all([requester, target])
    db.flush()
    departed = Employee(
        org_id=org.id,
        department_id=target.id,
        display_name="合成员工",
        employment_status="inactive",
    )
    db.add(departed)
    db.flush()
    users: dict[str, User] = {}
    members: dict[str, OrgMembership] = {}
    for role in ("manager", "admin", "requester"):
        user = User(
            username=str(uuid4()),
            email=f"{uuid4()}@example.com",
            hashed_password="unused",
        )
        db.add(user)
        db.flush()
        member = OrgMembership(org_id=org.id, user_id=user.id)
        db.add(member)
        db.flush()
        users[role], members[role] = user, member
    db.add_all(
        [
            RoleGrant(
                org_id=org.id,
                membership_id=members["manager"].id,
                role="dept_manager",
                department_id=target.id,
            ),
            RoleGrant(
                org_id=org.id,
                membership_id=members["admin"].id,
                role="org_admin",
            ),
            RoleGrant(
                org_id=org.id,
                membership_id=members["requester"].id,
                role="dept_manager",
                department_id=requester.id,
            ),
        ]
    )
    db.add(
        CapacitySetting(
            org_id=org.id, enabled=True, updated_by_user_id=users["admin"].id
        )
    )
    today = date.today()
    demand = CapacityDemand(
        org_id=org.id,
        requesting_department_id=requester.id,
        target_department_id=target.id,
        requester_user_id=users["requester"].id,
        capability_key="synthetic",
        capability_name="合成能力",
        headcount=1,
        allocation_percent=20,
        window_start=today,
        window_end=today + timedelta(days=30),
        committed_start_date=today,
        committed_end_date=today + timedelta(days=30),
        status="accepted",
        description="合成工作说明",
    )
    db.add(demand)
    db.commit()

    def submit(role: str) -> tuple[int, dict]:
        response = client.post(
            f"/api/v1/orgs/{org.id}/capacity/demands/{demand.id}/assignments",
            headers={
                "Authorization": f"Bearer {create_access_token(users[role].id, expires_delta=timedelta(minutes=30))}"
            },
            json={
                "employee_id": str(departed.id),
                "allocation_percent": 20,
                "start_date": today.isoformat(),
                "due_date": (today + timedelta(days=7)).isoformat(),
            },
        )
        return response.status_code, response.json()

    expected = {
        "detail": {
            "code": "employee_departed",
            "message": "该员工已不在职，请刷新后重新选择",
        }
    }
    try:
        assert submit("manager") == (409, expected)
        assert submit("admin")[0] == 404
        assert submit("requester")[0] == 404
        db.add(
            RoleGrant(
                org_id=org.id,
                membership_id=members["admin"].id,
                role="dept_manager",
                department_id=target.id,
            )
        )
        db.commit()
        assert submit("admin") == (409, expected)
    finally:
        db.rollback()
        db.execute(delete(CapacityDemand).where(CapacityDemand.org_id == org.id))
        db.execute(delete(CapacitySetting).where(CapacitySetting.org_id == org.id))
        db.commit()
        delete_test_organization(db, org.id)
