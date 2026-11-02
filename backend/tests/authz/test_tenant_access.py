import uuid
from collections.abc import Generator
from dataclasses import replace
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.authz.policies import (
    can_view_employee,
    can_view_evidence_content,
    employee_scope_clause,
)
from app.authz.principal import InvalidPrincipal, Principal
from app.core.security import create_access_token
from app.domains.audit.models import ActorKind
from app.domains.base import utc_now
from app.domains.capability.models import CapabilitySchemaVersion
from app.domains.evidence.models import Visibility
from app.domains.org.models import (
    Department,
    Employee,
    Organization,
    OrgMembership,
    Role,
    RoleGrant,
)
from app.domains.profile.models import ProfileProgress, ProfileVersion
from app.models import User
from app.worker.tenant import TenantNotFound, tenant_task
from tests.utils.org import cleanup_new_users, delete_test_organization


def headers(user: User) -> dict[str, str]:
    token = create_access_token(user.id, expires_delta=timedelta(minutes=5))
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def tenant_data(
    db: Session,
) -> Generator[tuple[list[Organization], list[Department], list[Employee], list[User]]]:
    orgs = [Organization(slug=str(uuid.uuid7()), name="Synthetic") for _ in range(2)]
    users = [
        User(
            email=f"{uuid.uuid7()}@example.com",
            hashed_password="unused",
            is_superuser=i == 4,
        )
        for i in range(6)
    ]
    db.add_all([*orgs, *users])
    db.flush()
    depts = [Department(org_id=orgs[0].id, name=str(i)) for i in range(3)]
    db.add_all(depts)
    db.flush()
    employees = [
        Employee(
            org_id=orgs[0].id, department_id=depts[i % 3].id, display_name=f"Shared {i}"
        )
        for i in range(5)
    ]
    employees[1].department_id = None
    employees.append(Employee(org_id=orgs[1].id, display_name="Shared foreign"))
    db.add_all(employees)
    db.flush()
    memberships = [
        OrgMembership(
            org_id=orgs[i == 3].id,
            user_id=users[i].id,
            employee_id=employees[5 if i == 3 else i].id if i else None,
        )
        for i in range(4)
    ]
    db.add_all(memberships)
    db.flush()
    db.add(
        RoleGrant(
            org_id=orgs[0].id,
            membership_id=memberships[0].id,
            role=Role.ORG_ADMIN,
            granted_by_user_id=users[5].id,
        )
    )
    for dept in depts[:2]:
        db.add(
            RoleGrant(
                org_id=orgs[0].id,
                membership_id=memberships[1].id,
                role=Role.DEPT_MANAGER,
                department_id=dept.id,
            )
        )
    schema = CapabilitySchemaVersion(org_id=orgs[0].id, version=1)
    db.add(schema)
    db.flush()
    db.add(ProfileProgress(org_id=orgs[0].id, employee_id=employees[0].id))
    for revision in (1, 2):
        db.add(
            ProfileVersion(
                org_id=orgs[0].id,
                subject_employee_id=employees[0].id,
                revision=revision,
                schema_version_id=schema.id,
                snapshot={},
                published_by={},
                published_at=utc_now(),
            )
        )
    db.commit()
    try:
        yield orgs, depts, employees, users
    finally:
        db.rollback()
        for org in orgs:
            delete_test_organization(db, org.id)


def test_policy_matrix(tenant_data: tuple, db: Session) -> None:
    orgs, depts, employees, users = tenant_data
    p = Principal(ActorKind.USER, orgs[0].id, users[0].id, None, False, frozenset())
    principals = [
        p,
        replace(p, is_org_admin=True),
        replace(p, employee_id=employees[2].id),
        replace(p, managed_department_ids=frozenset([depts[0].id])),
        Principal.system_worker(p.org_id),
    ]
    for index, principal in enumerate(principals):
        visible = set(
            db.exec(select(Employee.id).where(employee_scope_clause(principal))).all()
        )
        for employee in employees:
            own = index == 2 and employee.id == employees[2].id
            managed = index == 3 and employee.department_id == depts[0].id
            expected = employee.org_id == p.org_id and (
                index in (1, 4) or own or managed
            )
            assert can_view_employee(principal, employee) == expected
            assert (employee.id in visible) == expected
            for visibility in Visibility:
                content = expected and (
                    visibility == Visibility.STANDARD or index == 4 or own or managed
                )
                assert (
                    can_view_evidence_content(
                        principal, subject_employee=employee, visibility=visibility
                    )
                    == content
                )
    combined = replace(principals[3], is_org_admin=True, employee_id=employees[2].id)
    assert all(
        can_view_evidence_content(
            combined, subject_employee=e, visibility=Visibility.RESTRICTED
        )
        for e in (employees[0], employees[2])
    )
    with pytest.raises(InvalidPrincipal):
        replace(p, actor_type=ActorKind.AGENT)
    with pytest.raises(InvalidPrincipal):
        replace(p, actor_type=ActorKind.SEED)
    with pytest.raises(InvalidPrincipal):
        replace(p, user_id=None)
    with pytest.raises(InvalidPrincipal):
        replace(p, actor_type=ActorKind.SYSTEM_WORKER)


def test_employee_api(tenant_data: tuple, client: TestClient) -> None:
    orgs, depts, employees, users = tenant_data
    url = f"/api/v1/orgs/{orgs[0].id}"
    for index, expected in ((0, {0, 1, 2, 3, 4}), (1, {0, 1, 3, 4}), (2, {2})):
        auth = headers(users[index])
        response = client.get(f"{url}/employees", headers=auth)
        assert response.status_code == 200
        data = response.json()
        assert {e["id"] for e in data["data"]} == {
            str(employees[i].id) for i in expected
        }
        assert data["count"] == len(expected)
        assert client.get(f"{url}/departments", headers=auth).json()["count"] == 3
        for i, employee in enumerate(employees):
            detail = client.get(f"{url}/employees/{employee.id}", headers=auth)
            assert detail.status_code == (200 if i in expected else 404)
        search = client.get(
            f"{url}/employees", headers=auth, params={"q": "Shared"}
        ).json()
        assert search["count"] == len(expected)
        page = client.get(f"{url}/employees?skip=1&limit=1", headers=auth).json()
        assert page["count"] == len(expected) and len(page["data"]) == min(
            1, len(expected) - 1
        )
    for index in (1, 2):
        filtered = client.get(
            f"{url}/employees?department_id={depts[2 if index == 1 else 0].id}",
            headers=headers(users[index]),
        )
        assert filtered.status_code == 200 and filtered.json() == {
            "data": [],
            "count": 0,
        }
    for index in (3, 4, 5):
        for path in ("departments", "employees", f"employees/{employees[0].id}"):
            response = client.get(f"{url}/{path}", headers=headers(users[index]))
            assert (
                response.status_code == 404
                and response.json()["detail"] == "Organization not found"
            )
    auth = headers(users[0])
    detail = client.get(f"{url}/employees/{employees[0].id}", headers=auth).json()
    assert detail["department"]["id"] == str(depts[0].id)
    assert detail["progress"]["fill_status"] == "not_started"
    assert detail["latest_published"]["revision"] == 2
    assert "work_email" not in detail
    assert client.get(f"{url}/employees?q=foreign", headers=auth).json()["count"] == 0
    assert client.get(f"{url}/employees?q=%25", headers=auth).json()["count"] == 0
    assert client.get(f"{url}/employees?skip=-1", headers=auth).status_code == 422
    assert client.get(f"{url}/employees?limit=101", headers=auth).status_code == 422
    assert (
        client.get(f"{url}/employees/{uuid.uuid7()}", headers=auth).status_code == 404
    )
    assert (
        client.get(f"/api/v1/orgs/{uuid.uuid7()}/departments", headers=auth).status_code
        == 404
    )
    for index, roles in (
        (0, ["org_admin"]),
        (1, ["dept_manager", "employee"]),
        (2, ["employee"]),
        (4, []),
    ):
        memberships = client.get(
            "/api/v1/me/memberships", headers=headers(users[index])
        ).json()
        assert len(memberships) == (1 if roles else 0)
        if roles:
            assert memberships[0]["roles"] == roles
        if index == 1:
            assert set(memberships[0]["managed_department_ids"]) == {
                str(d.id) for d in depts[:2]
            }


def test_worker_and_user_cleanup(
    tenant_data: tuple, db: Session, client: TestClient
) -> None:
    orgs, _, employees, users = tenant_data

    def body(principal: Principal) -> uuid.UUID:
        assert principal == Principal.system_worker(orgs[0].id)
        assert can_view_employee(principal, employees[0])
        return principal.org_id

    assert tenant_task(orgs[0].id, body) == orgs[0].id
    with pytest.raises(TenantNotFound):
        tenant_task(uuid.uuid7(), body)
    for index in (0, 2, 5):
        for target, auth in (
            ("me", headers(users[index])),
            (str(users[index].id), headers(users[4])),
        ):
            response = client.delete(f"/api/v1/users/{target}", headers=auth)
            assert (
                response.status_code == 409
                and response.json()["detail"] == "User still belongs to an organization"
            )
    existing = set(db.exec(select(User.id)).all())
    disposable = User(email=f"{uuid.uuid7()}@example.com", hashed_password="unused")
    disposable_id = disposable.id
    db.add(disposable)
    db.commit()
    cleanup_new_users(db, existing - {users[0].id, users[5].id})
    db.commit()
    db.expire_all()
    assert db.get(User, disposable_id) is None
    assert db.get(User, users[0].id) is not None
    assert db.get(User, users[5].id) is not None
    assert db.get(User, users[4].id) is not None
