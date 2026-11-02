from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.security import create_access_token
from app.domains.audit.models import AuditEvent
from app.domains.capability.models import CapabilitySchemaVersion, SchemaStatus
from app.domains.org.models import (
    Department,
    Employee,
    EmploymentStatus,
    Organization,
    OrgMembership,
    Role,
    RoleGrant,
)
from app.domains.setup import steps
from app.domains.setup.models import SetupStatus, SetupStepState
from app.models import User
from tests.utils.org import delete_test_organization


@dataclass
class SetupTenant:
    org_id: UUID
    admin_id: UUID
    auth: dict[str, dict[str, str]]

    @property
    def url(self) -> str:
        return f"/api/v1/orgs/{self.org_id}"


@pytest.fixture
def tenant(db: Session) -> Generator[SetupTenant]:
    orgs = [Organization(slug=str(uuid4()), name="合成测试企业") for _ in range(2)]
    users = [User(username=str(uuid4()), hashed_password="unused") for _ in range(4)]
    db.add_all([*orgs, *users])
    db.flush()
    department = Department(org_id=orgs[0].id, name="测试部门")
    db.add(department)
    db.flush()
    for index, user in enumerate(users):
        org_id = orgs[1 if index == 3 else 0].id
        membership = OrgMembership(org_id=org_id, user_id=user.id)
        db.add(membership)
        db.flush()
        if index != 2:
            db.add(
                RoleGrant(
                    org_id=org_id,
                    membership_id=membership.id,
                    role=Role.DEPT_MANAGER if index == 1 else Role.ORG_ADMIN,
                    department_id=department.id if index == 1 else None,
                )
            )
    # Another tenant's ready data must not satisfy this tenant's checks.
    foreign_employee = Employee(
        org_id=orgs[1].id, employee_code="FOREIGN", display_name="合成员工"
    )
    db.add(foreign_employee)
    db.flush()
    db.add(
        CapabilitySchemaVersion(
            org_id=orgs[1].id, version=1, status=SchemaStatus.ACTIVE
        )
    )
    db.commit()
    auth = {
        role: {
            "Authorization": f"Bearer {create_access_token(user.id, timedelta(minutes=5))}"
        }
        for role, user in zip(
            ("admin", "manager", "employee", "foreign"), users, strict=True
        )
    }
    yield SetupTenant(orgs[0].id, users[0].id, auth)
    db.rollback()
    for org in orgs:
        delete_test_organization(db, org.id)
    for user in users:
        db.delete(user)
    db.commit()


def audits(db: Session, tenant: SetupTenant) -> list[AuditEvent]:
    return list(
        db.exec(select(AuditEvent).where(AuditEvent.org_id == tenant.org_id)).all()
    )


def test_new_setup_and_blockers(
    tenant: SetupTenant, client: TestClient, db: Session
) -> None:
    response = client.get(f"{tenant.url}/setup", headers=tenant.auth["admin"])
    assert response.status_code == 200
    data = response.json()
    assert not data["completed"] and data["next_step_key"] == "roster"
    assert [s["key"] for s in data["steps"]] == [
        "org_profile",
        "roster",
        "capabilities",
        "accounts",
    ]
    assert [s["order"] for s in data["steps"]] == [10, 20, 30, 40]
    assert all(s["required"] and not s["skippable"] for s in data["steps"])
    assert data["steps"][0]["status"] == "completed"
    assert data["steps"][0]["blockers"] == []
    for step in data["steps"][1:]:
        assert step["status"] == "pending" and step["blockers"]
        blocked = client.post(
            f"{tenant.url}/setup/steps/{step['key']}/complete",
            headers=tenant.auth["admin"],
        )
        assert blocked.status_code == 422
        assert blocked.json()["detail"] == {
            "code": "setup_step_blocked",
            "message": "这一步还不能完成",
            "blockers": step["blockers"],
        }
    assert (
        client.post(
            f"{tenant.url}/setup/steps/org_profile/complete",
            headers=tenant.auth["admin"],
        ).status_code
        == 200
    )
    assert db.get(SetupStepState, (tenant.org_id, "org_profile")) is None
    assert audits(db, tenant) == []


def test_complete_persist_and_extend(
    tenant: SetupTenant,
    client: TestClient,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    employee = Employee(
        org_id=tenant.org_id, employee_code="SYN01", display_name="合成员工"
    )
    inactive = Employee(
        org_id=tenant.org_id,
        employee_code="SYN02",
        display_name="已离职",
        employment_status=EmploymentStatus.INACTIVE,
    )
    no_code = Employee(org_id=tenant.org_id, display_name="无工号")
    schema = CapabilitySchemaVersion(
        org_id=tenant.org_id, version=1, status=SchemaStatus.DRAFT
    )
    db.add_all([employee, inactive, no_code, schema])
    db.commit()
    auth = tenant.auth["admin"]
    assert (
        client.post(
            f"{tenant.url}/setup/steps/capabilities/complete", headers=auth
        ).status_code
        == 422
    )
    blocked = client.post(f"{tenant.url}/setup/steps/accounts/complete", headers=auth)
    assert blocked.json()["detail"]["blockers"] == ["还有 1 名在职员工没有账号"]
    # An unrelated admin membership does not count as an employee account.
    membership = db.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == tenant.org_id,
            OrgMembership.user_id != tenant.admin_id,
        )
    ).first()
    membership.employee_id = employee.id
    db.add(membership)
    schema.status = SchemaStatus.ACTIVE
    db.add(schema)
    db.commit()
    for key in ("roster", "capabilities", "accounts"):
        url = f"{tenant.url}/setup/steps/{key}/complete"
        response = client.post(url, headers=auth)
        assert response.status_code == 200
        assert client.post(url, headers=auth).json() == response.json()
        state = db.get(SetupStepState, (tenant.org_id, key))
        assert state.status == SetupStatus.COMPLETED
        assert (
            state.updated_by_user_id == tenant.admin_id and state.updated_at is not None
        )
    assert response.json()["completed"] and response.json()["next_step_key"] is None
    events = audits(db, tenant)
    assert len(events) == 3
    assert {e.details["step_key"] for e in events} == {
        "roster",
        "capabilities",
        "accounts",
    }
    assert all(
        e.action == "setup.step_completed" and e.actor_id == str(tenant.admin_id)
        for e in events
    )

    # Saved decisions survive later changes, and their checks are never called.
    def unexpected_check(_session: Session, _org_id: UUID) -> list[str]:
        pytest.fail("Completed steps must not run checks")

    employee.employment_status = EmploymentStatus.INACTIVE
    schema.status = SchemaStatus.RETIRED
    db.add_all([employee, schema])
    db.commit()
    original = [replace(s, check=unexpected_check) for s in steps.STEPS]
    monkeypatch.setattr(steps, "STEPS", original)
    assert client.get(f"{tenant.url}/setup", headers=auth).json()["completed"]
    assert (
        client.post(
            f"{tenant.url}/setup/steps/roster/complete", headers=auth
        ).status_code
        == 200
    )
    extra = steps.SetupStep(
        "future", "新增设置", "测试扩展", 25, True, False, steps.check_org_profile
    )
    monkeypatch.setattr(steps, "STEPS", [extra, *original])
    data = client.get(f"{tenant.url}/setup", headers=auth).json()
    assert not data["completed"] and data["next_step_key"] == "future"
    assert [s["order"] for s in data["steps"]] == [10, 20, 25, 30, 40]


def test_skips_and_unknown_steps(
    tenant: SetupTenant,
    client: TestClient,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extra = steps.SetupStep(
        "optional", "可选设置", "测试跳过", 50, False, True, steps.check_org_profile
    )
    monkeypatch.setattr(steps, "STEPS", [*steps.STEPS, extra])
    base = f"{tenant.url}/setup/steps"
    auth = tenant.auth["admin"]
    assert (
        client.post(f"{base}/roster/skip", headers=auth).json()["detail"]
        == "这一步不能跳过"
    )
    assert client.post(f"{base}/roster/skip", headers=auth).status_code == 422
    assert client.post(f"{base}/org_profile/skip", headers=auth).status_code == 409
    for action in ("complete", "skip"):
        response = client.post(f"{base}/unknown/{action}", headers=auth)
        assert (
            response.status_code == 404
            and response.json()["detail"] == "没有这个设置步骤"
        )
    response = client.post(f"{base}/optional/skip", headers=auth)
    assert (
        response.status_code == 200
        and response.json()["steps"][-1]["status"] == "skipped"
    )
    monkeypatch.setattr(
        steps,
        "STEPS",
        [*steps.STEPS[:-1], replace(extra, check=lambda _s, _o: ["尚未就绪"])],
    )
    assert (
        client.get(f"{tenant.url}/setup", headers=auth).json()["steps"][-1]["blockers"]
        == []
    )
    assert client.post(f"{base}/optional/skip", headers=auth).status_code == 200
    assert len(audits(db, tenant)) == 1
    assert audits(db, tenant)[0].action == "setup.step_skipped"
    assert client.post(f"{base}/optional/complete", headers=auth).status_code == 422
    monkeypatch.setattr(steps, "STEPS", [*steps.STEPS[:-1], extra])
    assert client.post(f"{base}/optional/complete", headers=auth).status_code == 200
    assert client.post(f"{base}/optional/skip", headers=auth).status_code == 409
    db.expire_all()
    assert (
        db.get(SetupStepState, (tenant.org_id, "optional")).status
        == SetupStatus.COMPLETED
    )
    assert len(audits(db, tenant)) == 2


def test_concurrent_completion(
    tenant: SetupTenant, client: TestClient, db: Session
) -> None:
    db.add(
        CapabilitySchemaVersion(
            org_id=tenant.org_id, version=1, status=SchemaStatus.ACTIVE
        )
    )
    db.commit()

    def complete(_index: int) -> int:
        return client.post(
            f"{tenant.url}/setup/steps/capabilities/complete",
            headers=tenant.auth["admin"],
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(complete, range(2))) == [200, 200]
    assert len(audits(db, tenant)) == 1


def test_rename(tenant: SetupTenant, client: TestClient, db: Session) -> None:
    response = client.patch(
        tenant.url, headers=tenant.auth["admin"], json={"name": "  新企业名称  "}
    )
    assert response.status_code == 200
    assert response.json()["name"] == "新企业名称"
    assert response.json()["id"] == str(tenant.org_id)
    db.expire_all()
    assert db.get(Organization, tenant.org_id).name == "新企业名称"
    event = audits(db, tenant)[0]
    assert event.action == "org.renamed"
    assert event.details == {"old_name": "合成测试企业", "new_name": "新企业名称"}
    for name in ("", " \t\n ", "长" * 101):
        assert (
            client.patch(
                tenant.url, headers=tenant.auth["admin"], json={"name": name}
            ).status_code
            == 422
        )
    assert len(audits(db, tenant)) == 1
    assert (
        client.patch(
            tenant.url, headers=tenant.auth["admin"], json={"name": "长" * 100}
        ).status_code
        == 200
    )


@pytest.mark.parametrize(
    "role,expected", [("manager", 403), ("employee", 403), ("foreign", 404)]
)
def test_setup_permissions(
    tenant: SetupTenant, client: TestClient, db: Session, role: str, expected: int
) -> None:
    auth = tenant.auth[role]
    responses = [
        client.get(f"{tenant.url}/setup", headers=auth),
        client.post(f"{tenant.url}/setup/steps/roster/complete", headers=auth),
        client.post(f"{tenant.url}/setup/steps/roster/skip", headers=auth),
        client.patch(tenant.url, headers=auth, json={"name": "禁止修改"}),
    ]
    assert all(r.status_code == expected for r in responses)
    assert audits(db, tenant) == []
