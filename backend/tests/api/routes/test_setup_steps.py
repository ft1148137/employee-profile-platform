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
from app.domains.change_request.models import (
    ProfileChangeRequest,
    RequestOrigin,
    RequestStatus,
)
from app.domains.model.models import ModelSetting
from app.domains.org.models import (
    Department,
    Employee,
    EmploymentStatus,
    Organization,
    OrgMembership,
    Role,
    RoleGrant,
    RosterImport,
)
from app.domains.profile.models import InferenceStatus, ProfileProgress
from app.domains.setup import steps
from app.domains.setup.models import SetupStatus, SetupStepState
from app.domains.source.models import SourceConnection, SyncRun
from app.models import User
from tests.utils.org import delete_test_organization


@dataclass
class SetupTenant:
    org_id: UUID
    admin_id: UUID
    foreign_org_id: UUID
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
    yield SetupTenant(orgs[0].id, users[0].id, orgs[1].id, auth)
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
    assert not data["completed"] and data["next_step_key"] == "model"
    assert [s["key"] for s in data["steps"]] == [
        "org_profile",
        "roster",
        "capabilities",
        "accounts",
        "sources",
        "identities",
        "model",
        "approval_policy",
        "initial_profiles",
    ]
    assert [s["order"] for s in data["steps"]] == [10, 20, 30, 40, 50, 60, 70, 80, 90]
    assert {s["key"] for s in data["steps"] if s["required"]} == {
        "org_profile",
        "model",
    }
    assert next(s for s in data["steps"] if s["key"] == "model")["skippable"]
    assert data["steps"][-4]["blockers"] == []
    assert data["steps"][0]["status"] == "completed"
    assert data["steps"][0]["blockers"] == []
    for step in [
        s
        for s in data["steps"][1:]
        if s["key"] not in {"identities", "approval_policy", "initial_profiles"}
    ]:
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
    assert (
        client.post(
            f"{tenant.url}/setup/steps/roster/complete", headers=auth
        ).status_code
        == 422
    )
    db.add(
        RosterImport(
            org_id=tenant.org_id,
            status="applied",
            filename="synthetic.csv",
            file_sha256="a" * 64,
            rows=[],
            column_mapping={},
            issues=[],
            summary={},
            base_revision=0,
            uploaded_by_user_id=tenant.admin_id,
        )
    )
    db.commit()
    for key in ("roster", "capabilities", "accounts", "identities"):
        url = f"{tenant.url}/setup/steps/{key}/complete"
        response = client.post(url, headers=auth)
        assert response.status_code == 200
        assert client.post(url, headers=auth).json() == response.json()
        state = db.get(SetupStepState, (tenant.org_id, key))
        assert state.status == SetupStatus.COMPLETED
        assert (
            state.updated_by_user_id == tenant.admin_id and state.updated_at is not None
        )
    assert (
        not response.json()["completed"] and response.json()["next_step_key"] == "model"
    )
    assert (
        client.post(f"{tenant.url}/setup/steps/sources/skip", headers=auth).status_code
        == 200
    )
    db.add(
        ModelSetting(
            org_id=tenant.org_id, enabled=True, updated_by_user_id=tenant.admin_id
        )
    )
    db.commit()
    assert (
        client.post(
            f"{tenant.url}/setup/steps/model/complete", headers=auth
        ).status_code
        == 200
    )
    assert (
        client.get(f"{tenant.url}/setup", headers=auth).json()["next_step_key"] is None
    )
    assert (
        client.post(
            f"{tenant.url}/setup/steps/approval_policy/complete", headers=auth
        ).status_code
        == 200
    )
    db.add_all(
        [
            ProfileChangeRequest(
                org_id=tenant.org_id,
                subject_employee_id=e.id,
                origin=RequestOrigin.INITIAL_SYNC,
                status=RequestStatus.PENDING_OWNER,
                author={},
            )
            for e in (employee, no_code)
        ]
    )
    db.commit()
    response = client.post(
        f"{tenant.url}/setup/steps/initial_profiles/complete", headers=auth
    )
    assert response.status_code == 200
    assert response.json()["completed"] and response.json()["next_step_key"] is None
    events = audits(db, tenant)
    assert len(events) == 8
    assert {e.details["step_key"] for e in events} == {
        "roster",
        "capabilities",
        "accounts",
        "sources",
        "identities",
        "model",
        "approval_policy",
        "initial_profiles",
    }
    assert all(
        e.action
        == (
            "setup.step_skipped"
            if e.details["step_key"] in {"sources"}
            else "setup.step_completed"
        )
        and e.actor_id == str(tenant.admin_id)
        for e in events
    )

    # Saved decisions survive later changes, and their checks are never called.
    def unexpected_check(_session: Session, _org_id: UUID) -> list[str]:
        pytest.fail("Completed steps must not run checks")

    employee.employment_status = EmploymentStatus.INACTIVE
    schema.status = SchemaStatus.RETIRED
    db.add_all([employee, schema])
    db.commit()
    original = [
        s if s.key == "model" else replace(s, check=unexpected_check)
        for s in steps.STEPS
    ]
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
    assert [s["order"] for s in data["steps"]] == [
        10,
        20,
        25,
        30,
        40,
        50,
        60,
        70,
        80,
        90,
    ]


@pytest.mark.parametrize(
    "action,status", [("complete", "completed"), ("skip", "skipped")]
)
def test_identities_optional(
    tenant: SetupTenant, client: TestClient, action: str, status: str
) -> None:
    response = client.post(
        f"{tenant.url}/setup/steps/identities/{action}", headers=tenant.auth["admin"]
    )
    assert response.status_code == 200
    step = next(s for s in response.json()["steps"] if s["key"] == "identities")
    assert step["status"] == status and step["blockers"] == []


def test_skips_and_unknown_steps(
    tenant: SetupTenant,
    client: TestClient,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extra = steps.SetupStep(
        "optional", "可选设置", "测试跳过", 100, False, True, steps.check_org_profile
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


def test_sources_success_is_tenant_scoped(
    tenant: SetupTenant, client: TestClient, db: Session
) -> None:
    auth = tenant.auth["admin"]
    url = f"{tenant.url}/setup/steps/sources"
    runs = []
    for org_id, status in (
        (tenant.org_id, "failed"),
        (tenant.foreign_org_id, "succeeded"),
    ):
        member = db.exec(
            select(OrgMembership).where(OrgMembership.org_id == org_id)
        ).first()
        assert member is not None
        connection = SourceConnection(
            org_id=org_id, kind="csv", updated_by_user_id=member.user_id
        )
        db.add(connection)
        db.flush()
        run = SyncRun(
            org_id=org_id,
            connection_id=connection.id,
            requested_by_user_id=member.user_id,
            status=status,
        )
        db.add(run)
        runs.append(run)
    db.commit()
    run = runs[0]
    for status in ("failed", "queued", "running"):
        run.status = status
        db.add(run)
        db.commit()
        response = client.post(f"{url}/complete", headers=auth)
        assert response.status_code == 422
        assert response.json()["detail"]["blockers"] == [
            "还没有成功同步或导入过工作记录"
        ]
    assert client.post(f"{url}/skip", headers=auth).status_code == 200
    run.status = "succeeded"
    db.add(run)
    db.commit()
    response = client.post(f"{url}/complete", headers=auth)
    assert response.status_code == 200
    step = next(s for s in response.json()["steps"] if s["key"] == "sources")
    assert step["status"] == "completed" and not step["blockers"]


def test_model_step(tenant: SetupTenant, client: TestClient, db: Session) -> None:
    from app.domains.model.models import ModelSetting

    url = f"{tenant.url}/setup/steps/model"
    auth = tenant.auth["admin"]
    assert client.post(f"{url}/complete", headers=auth).status_code == 422
    assert client.post(f"{url}/skip", headers=auth).json()["completed"]
    db.add(
        ModelSetting(
            org_id=tenant.org_id, enabled=True, updated_by_user_id=tenant.admin_id
        )
    )
    db.commit()
    assert client.post(f"{url}/complete", headers=auth).status_code == 200


@pytest.mark.parametrize("old_status", [SetupStatus.COMPLETED, SetupStatus.SKIPPED])
def test_upgrade_preserves_completed_setup(
    tenant: SetupTenant, client: TestClient, db: Session, old_status: SetupStatus
) -> None:
    db.add_all(
        [
            SetupStepState(
                org_id=tenant.org_id,
                step_key=s.key,
                status=old_status if s.key == "model" else SetupStatus.COMPLETED,
                updated_by_user_id=tenant.admin_id,
            )
            for s in steps.STEPS
            if not s.implicit
        ]
    )
    db.commit()
    data = client.get(f"{tenant.url}/setup", headers=tenant.auth["admin"]).json()
    assert data["completed"] and data["next_step_key"] is None
    model = next(s for s in data["steps"] if s["key"] == "model")
    assert model["required"] and model["skippable"] and not model["blockers"]
    assert client.post(
        f"{tenant.url}/setup/steps/model/complete", headers=tenant.auth["admin"]
    ).status_code == (200 if old_status == SetupStatus.COMPLETED else 422)
    db.add(
        ModelSetting(
            org_id=tenant.org_id, enabled=True, updated_by_user_id=tenant.admin_id
        )
    )
    db.commit()
    result = client.post(
        f"{tenant.url}/setup/steps/model/complete", headers=tenant.auth["admin"]
    )
    assert result.status_code == 200 and result.json()["completed"]


def test_initial_profiles_checks(
    tenant: SetupTenant, client: TestClient, db: Session
) -> None:
    base = f"{tenant.url}/setup/steps"
    auth = tenant.auth["admin"]
    employee = Employee(org_id=tenant.org_id, display_name="应起草")
    excluded = Employee(
        org_id=tenant.org_id, display_name="本人退出", auto_draft_opt_out=True
    )
    department = Department(
        org_id=tenant.org_id, name="已关闭部门", auto_draft_disabled=True
    )
    db.add_all([employee, excluded, department])
    db.flush()
    db.add(
        Employee(
            org_id=tenant.org_id, display_name="部门关闭", department_id=department.id
        )
    )
    foreign = db.exec(
        select(Employee).where(
            Employee.org_id != tenant.org_id, Employee.employee_code == "FOREIGN"
        )
    ).first()
    assert foreign is not None
    db.add_all(
        [
            ProfileChangeRequest(
                org_id=foreign.org_id,
                subject_employee_id=foreign.id,
                origin=RequestOrigin.INITIAL_SYNC,
                status=RequestStatus.PENDING_OWNER,
                author={},
            ),
            ProfileProgress(
                org_id=foreign.org_id,
                employee_id=foreign.id,
                inference_status=InferenceStatus.RUNNING,
            ),
            ProfileProgress(
                org_id=tenant.org_id,
                employee_id=excluded.id,
                inference_status=InferenceStatus.PENDING,
            ),
        ]
    )
    db.commit()
    assert client.post(f"{base}/initial_profiles/skip", headers=auth).status_code == 422
    assert steps.check_initial_profiles(db, tenant.org_id) == [
        "还有 1 名员工没有初始画像"
    ]
    blocked = client.post(f"{base}/initial_profiles/complete", headers=auth)
    assert blocked.status_code == 422
    assert blocked.json()["detail"]["blockers"] == ["还有 1 名员工没有初始画像"]
    db.add(
        ProfileChangeRequest(
            org_id=tenant.org_id,
            subject_employee_id=employee.id,
            origin=RequestOrigin.INITIAL_SYNC,
            status=RequestStatus.PENDING_OWNER,
            author={},
        )
    )
    progress = ProfileProgress(
        org_id=tenant.org_id,
        employee_id=employee.id,
        inference_status=InferenceStatus.PENDING,
    )
    db.add(progress)
    db.commit()
    assert steps.check_initial_profiles(db, tenant.org_id) == ["初始画像还在生成"]
    progress.inference_status = InferenceStatus.RUNNING
    db.add(progress)
    db.commit()
    assert (
        client.post(f"{base}/initial_profiles/complete", headers=auth).status_code
        == 422
    )
    progress.inference_status = InferenceStatus.GENERATED
    db.add(progress)
    db.commit()
    assert (
        client.post(f"{base}/initial_profiles/complete", headers=auth).status_code
        == 200
    )


def test_two_step_completion_and_home_todos(
    tenant: SetupTenant, client: TestClient, db: Session
) -> None:
    auth = tenant.auth["admin"]
    assert client.post(f"{tenant.url}/setup/steps/model/skip", headers=auth).json()[
        "completed"
    ]
    overview = client.get(f"{tenant.url}/admin/overview", headers=auth).json()
    todo = next(t for t in overview["todos"] if t["kind"] == "setup_step")
    assert todo["count"] == 7
    db.add(
        ModelSetting(
            org_id=tenant.org_id, enabled=True, updated_by_user_id=tenant.admin_id
        )
    )
    db.commit()
    result = client.post(
        f"{tenant.url}/setup/steps/model/complete", headers=auth
    ).json()
    assert result["completed"] and result["next_step_key"] is None
    assert (
        next(s for s in result["steps"] if s["key"] == "roster")["status"] == "pending"
    )


def test_roster_import_auto_completes_home_todo(
    tenant: SetupTenant, client: TestClient, db: Session
) -> None:
    from app.domains.setup.wizard import read_setup

    auth = tenant.auth["admin"]
    before = {s.key: s for s in read_setup(db, tenant.org_id).steps}
    assert before["roster"].status == "pending" and before["roster"].blockers
    assert before["initial_profiles"].status == "pending"
    assert before["initial_profiles"].blockers == ["还没有在职员工，请先导入员工名单"]
    uploaded = client.post(
        f"{tenant.url}/roster/imports",
        headers=auth,
        files={
            "file": (
                "synthetic.csv",
                "工号,姓名,部门,职位,上级工号,邮箱\n001,合成员工,研发部,,,synthetic@example.com\n".encode(),
                "text/csv",
            )
        },
    )
    assert uploaded.status_code == 200
    preview = uploaded.json()
    assert (
        client.post(
            f"{tenant.url}/roster/imports/{preview['id']}/apply",
            headers=auth,
            json={"base_revision": preview["base_revision"]},
        ).status_code
        == 200
    )
    actual = {s.key: s for s in read_setup(db, tenant.org_id).steps}
    assert actual["roster"].status == "completed" and not actual["roster"].blockers
    assert actual["identities"].status == "pending"
    assert actual["approval_policy"].status == "pending"
    assert db.get(SetupStepState, (tenant.org_id, "roster")) is None
    todo = next(
        t
        for t in client.get(f"{tenant.url}/admin/overview", headers=auth).json()[
            "todos"
        ]
        if t["kind"] == "setup_step"
    )
    assert "导入员工名单" not in todo["title"]
    client.post(f"{tenant.url}/setup/steps/model/skip", headers=auth)
    db.add(
        ModelSetting(
            org_id=tenant.org_id, enabled=True, updated_by_user_id=tenant.admin_id
        )
    )
    db.commit()
    assert (
        next(s for s in read_setup(db, tenant.org_id).steps if s.key == "model").status
        == "skipped"
    )
    db.delete(db.get(SetupStepState, (tenant.org_id, "model")))
    db.commit()
    assert read_setup(db, tenant.org_id).completed
