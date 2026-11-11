from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, inspect, text
from sqlmodel import Session, select

from app.domains.audit.models import AuditEvent
from app.domains.change_request.models import ChangeItem, ProfileChangeRequest
from app.domains.drafting import incremental
from app.domains.drafting.settings import drafting_scope
from app.domains.evidence.models import Evidence
from app.domains.org.models import Department, Employee, Organization, OrgMembership
from app.domains.profile.models import ProfileClaim
from app.models import User
from tests.api.routes.test_roster import apply, upload
from tests.api.routes.test_setup_steps import SetupTenant
from tests.api.routes.test_setup_steps import tenant as tenant
from tests.utils.migrations import migrate


def snapshot(db: Session, org_id: UUID) -> list:
    db.expire_all()
    return [
        [
            r.model_dump(mode="json")
            for r in db.exec(select(m).where(m.org_id == org_id).order_by(m.id)).all()
        ]
        for m in (Evidence, ProfileClaim, ProfileChangeRequest, ChangeItem)
    ]


def test_settings_and_own_choice(demo: tuple, client: TestClient, db: Session) -> None:
    url, people, auth = demo
    org_id, employee = people["E02"].org_id, people["E02"]
    settings_url, own_url = f"{url}/drafting-settings", f"{url}/me/auto-draft"
    before = snapshot(db, org_id)
    assert client.get(settings_url, headers=auth["admin"]).json() == {"enabled": False}
    for enabled in (False, True, True, False):
        result = client.put(
            settings_url, headers=auth["admin"], json={"enabled": enabled}
        )
        assert result.status_code == 200 and result.json() == {"enabled": enabled}
    audits = db.exec(
        select(AuditEvent)
        .where(
            AuditEvent.org_id == org_id,
            AuditEvent.action == "drafting_settings.updated",
        )
        .order_by(AuditEvent.created_at, AuditEvent.id)
    ).all()
    assert [a.details for a in audits] == [
        {"before": {"enabled": False}, "after": {"enabled": True}},
        {"before": {"enabled": True}, "after": {"enabled": False}},
    ]
    for enabled in (False, False, True):
        result = client.put(
            own_url, headers=auth["employee"], json={"enabled": enabled}
        )
        assert result.json() == {"enabled": enabled, "org_disabled": True}
        assert client.get(own_url, headers=auth["employee"]).json() == result.json()
        assert (employee.id in drafting_scope(db, org_id).excluded_employee_ids) == (
            not enabled
        )
    client.put(settings_url, headers=auth["admin"], json={"enabled": True})
    assert client.get(own_url, headers=auth["employee"]).json() == {
        "enabled": True,
        "org_disabled": False,
    }
    changes = db.exec(
        select(AuditEvent)
        .where(
            AuditEvent.org_id == org_id,
            AuditEvent.action == "employee.auto_draft_changed",
        )
        .order_by(AuditEvent.created_at, AuditEvent.id)
    ).all()
    assert [a.details for a in changes] == [{"enabled": False}, {"enabled": True}]
    assert all(a.target_id == str(employee.id) for a in changes)
    assert snapshot(db, org_id) == before


@pytest.mark.parametrize(
    "role,status", [("manager", 403), ("employee", 403), ("foreign", 404)]
)
def test_permissions(
    tenant: SetupTenant, client: TestClient, role: str, status: int
) -> None:
    url = f"{tenant.url}/drafting-settings"
    assert client.get(url, headers=tenant.auth[role]).status_code == status
    assert (
        client.put(url, headers=tenant.auth[role], json={"enabled": True}).status_code
        == status
    )
    for method in ("get", "put"):
        response = client.request(
            method,
            f"{tenant.url}/me/auto-draft",
            headers=tenant.auth[role],
            **({"json": {"enabled": False}} if method == "put" else {}),
        )
        assert response.status_code == 404


def test_invalid_settings_are_atomic(
    demo: tuple, client: TestClient, db: Session
) -> None:
    url, people, auth = demo
    path = f"{url}/drafting-settings"
    before = client.get(path, headers=auth["admin"]).json()
    assert (
        client.put(
            path,
            headers=auth["admin"],
            json={
                "enabled": True,
                "disabled_department_ids": [str(people["E03"].department_id)],
            },
        ).status_code
        == 422
    )
    assert client.get(path, headers=auth["admin"]).json() == before
    assert (
        client.put(
            f"{url}/me/auto-draft", headers=auth["admin"], json={"enabled": False}
        ).status_code
        == 404
    )
    assert (
        client.put(
            f"{url}/me/auto-draft",
            headers=auth["employee"],
            json={"enabled": False, "employee_id": str(people["E03"].id)},
        ).status_code
        == 422
    )
    db.expire_all()
    assert not db.get(Employee, people["E03"].id).auto_draft_opt_out


def test_disabled_triggers_do_not_queue(
    demo: tuple, client: TestClient, db: Session
) -> None:
    url, people, auth = demo
    org = people["E02"].org_id
    message = "自动起草未打开，请先在「数据与 AI → 模型」里打开"

    def jobs() -> int:
        return db.execute(
            text("SELECT count(*) FROM procrastinate_jobs WHERE args->>'org_id'=:org"),
            {"org": str(org)},
        ).scalar_one()

    before = jobs()
    response = client.post(
        f"{url}/initial-profiles/draft", headers=auth["admin"], json={"scope": "all"}
    )
    assert response.status_code == 422 and response.json()["detail"] == message
    dictionary = client.get(f"{url}/capabilities/editor", headers=auth["admin"]).json()
    response = client.put(
        f"{url}/capabilities",
        headers=auth["admin"],
        json={
            "definitions": dictionary["definitions"],
            "expected_version": dictionary["expected_version"],
            "redraft": True,
        },
    )
    assert response.status_code == 422 and response.json()["detail"] == message
    assert (
        client.get(f"{url}/capabilities/editor", headers=auth["admin"]).json()
        == dictionary
    )
    incremental.queue_incremental(db, org)
    db.commit()
    assert jobs() == before


def test_scope_all_departments_and_tenant_isolation(
    tenant: SetupTenant, db: Session
) -> None:
    root = Department(org_id=tenant.org_id, name="上级")
    db.add(root)
    db.flush()
    child = Department(org_id=tenant.org_id, name="下级", parent_department_id=root.id)
    db.add(child)
    db.flush()
    people = [
        Employee(org_id=tenant.org_id, display_name="合成员工", department_id=d.id)
        for d in (root, child)
    ]
    opted = Employee(
        org_id=tenant.org_id, display_name="自主退出", auto_draft_opt_out=True
    )
    db.add_all([*people, opted])
    foreign = db.exec(
        select(Employee).where(
            Employee.org_id != tenant.org_id, Employee.employee_code == "FOREIGN"
        )
    ).one()
    foreign.auto_draft_opt_out = True
    db.add(foreign)
    db.commit()
    assert drafting_scope(db, tenant.org_id).excluded_employee_ids == {opted.id}
    assert drafting_scope(db, foreign.org_id).excluded_employee_ids == {foreign.id}


def test_roster_preserves_preferences(
    tenant: SetupTenant, client: TestClient, db: Session
) -> None:
    client.headers.update(tenant.auth["admin"])
    data = "工号,姓名,部门,邮箱\nSYN,合成员工,测试部门,syn@example.com\n".encode()
    assert apply(client, tenant, upload(client, tenant, data)).status_code == 200
    employee = db.exec(select(Employee).where(Employee.org_id == tenant.org_id)).one()
    employee.auto_draft_opt_out = True
    db.add(employee)
    db.commit()
    assert apply(client, tenant, upload(client, tenant, data)).status_code == 200
    db.refresh(employee)
    assert employee.auto_draft_opt_out


def test_migration_roundtrip(migration_engine: Engine) -> None:
    with Session(migration_engine) as db:
        org = Organization(slug="synthetic-migration", name="合成企业")
        user = User(
            username="migration@example.com",
            email="migration@example.com",
            hashed_password="synthetic",
        )
        db.add_all([org, user])
        db.flush()
        db.add(OrgMembership(org_id=org.id, user_id=user.id))
        department = Department(org_id=org.id, name="旧关闭部门")
        employee = Employee(
            org_id=org.id, display_name="本人退出", auto_draft_opt_out=True
        )
        db.add_all([department, employee])
        db.commit()
        org_id, user_id, dept_id, employee_id = (
            org.id,
            user.id,
            department.id,
            employee.id,
        )
    migrate(migration_engine, "downgrade", "b283001bc001")
    with migration_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO drafting_setting (org_id, updated_by_user_id, updated_at, excluded_capability_keys) VALUES (:org, :user, now(), '[\"backend_api\"]'::jsonb)"
            ),
            {"org": org_id, "user": user_id},
        )
        conn.execute(
            text(
                "UPDATE department SET auto_draft_disabled=true WHERE org_id=:org AND id=:id"
            ),
            {"org": org_id, "id": dept_id},
        )
    for _ in range(2):
        migrate(migration_engine, "upgrade", "head")
        assert "auto_draft_disabled" not in {
            c["name"] for c in inspect(migration_engine).get_columns("department")
        }
        with migration_engine.connect() as conn:
            assert (
                conn.execute(
                    text(
                        "SELECT auto_draft_enabled FROM drafting_setting WHERE org_id=:org"
                    ),
                    {"org": org_id},
                ).scalar_one()
                is False
            )
            assert (
                conn.execute(
                    text(
                        "SELECT auto_draft_opt_out FROM employee WHERE org_id=:org AND id=:id"
                    ),
                    {"org": org_id, "id": employee_id},
                ).scalar_one()
                is True
            )
            assert (
                len(conn.execute(text("SELECT version_num FROM alembic_version")).all())
                == 1
            )
        migrate(migration_engine, "downgrade", "b283001bc001")
        with migration_engine.connect() as conn:
            assert (
                conn.execute(
                    text(
                        "SELECT excluded_capability_keys FROM drafting_setting WHERE org_id=:org"
                    ),
                    {"org": org_id},
                ).scalar_one()
                == []
            )
            assert (
                conn.execute(
                    text(
                        "SELECT auto_draft_disabled FROM department WHERE org_id=:org AND id=:id"
                    ),
                    {"org": org_id, "id": dept_id},
                ).scalar_one()
                is False
            )
    migrate(migration_engine, "upgrade", "head")
