from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, inspect
from sqlmodel import Session, select

from app.core.db import engine
from app.domains.audit.models import AuditEvent
from app.domains.change_request.models import ChangeItem, ProfileChangeRequest
from app.domains.drafting.settings import drafting_scope
from app.domains.evidence.models import Evidence
from app.domains.org.models import Department, Employee
from app.domains.profile.models import ProfileClaim
from tests.api.routes.test_profiles import demo as demo
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

    def audits() -> list[AuditEvent]:
        return list(
            db.exec(
                select(AuditEvent)
                .where(
                    AuditEvent.org_id == org_id,
                    AuditEvent.action.in_(
                        ["drafting_settings.updated", "employee.auto_draft_changed"]
                    ),
                )
                .order_by(AuditEvent.created_at, AuditEvent.id)
            ).all()
        )

    empty = {"excluded_capability_keys": [], "disabled_department_ids": []}
    assert client.get(settings_url, headers=auth["admin"]).json() == {
        **empty,
        "excluded_employee_count": 0,
        "opted_out_count": 0,
        "updated_at": None,
    }
    assert (
        client.put(settings_url, headers=auth["admin"], json=empty).status_code == 200
    )
    assert not audits()
    body = {
        "excluded_capability_keys": ["backend_api"],
        "disabled_department_ids": [str(employee.department_id)],
    }
    response = client.put(settings_url, headers=auth["admin"], json=body)
    assert response.status_code == 200, response.text
    assert drafting_scope(db, org_id).excluded_capability_keys == {"backend_api"}
    assert (
        response.json()["updated_at"] and response.json()["excluded_employee_count"] > 0
    )
    assert (
        client.put(settings_url, headers=auth["admin"], json=body).json()
        == response.json()
    )
    assert len(audits()) == 1
    assert audits()[0].details == {
        "added_capability_keys": ["backend_api"],
        "removed_capability_keys": [],
        "added_department_ids": body["disabled_department_ids"],
        "removed_department_ids": [],
    }
    assert client.get(own_url, headers=auth["employee"]).json() == {
        "enabled": True,
        "department_disabled": True,
    }
    for enabled in (False, False, True):
        result = client.put(
            own_url, headers=auth["employee"], json={"enabled": enabled}
        )
        assert result.json() == {"enabled": enabled, "department_disabled": True}
        assert client.get(own_url, headers=auth["employee"]).json() == result.json()
        settings = client.get(settings_url, headers=auth["admin"]).json()
        assert settings["opted_out_count"] == int(not enabled)
        assert set(settings) == {
            *empty,
            "excluded_employee_count",
            "opted_out_count",
            "updated_at",
        }
    changes = [a for a in audits() if a.action == "employee.auto_draft_changed"]
    assert [a.details for a in changes] == [{"enabled": False}, {"enabled": True}]
    assert all(
        a.target_id == str(employee.id) and a.target_type == "employee" for a in changes
    )
    assert (
        client.put(settings_url, headers=auth["admin"], json=empty).status_code == 200
    )
    assert audits()[-1].details == {
        "added_capability_keys": [],
        "removed_capability_keys": ["backend_api"],
        "added_department_ids": [],
        "removed_department_ids": body["disabled_department_ids"],
    }
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
        client.put(
            url,
            headers=tenant.auth[role],
            json={"excluded_capability_keys": [], "disabled_department_ids": []},
        ).status_code
        == status
    )
    for method in ("get", "put"):
        response = client.request(
            method,
            f"{tenant.url}/me/auto-draft",
            headers=tenant.auth[role],
            **({"json": {"enabled": False}} if method == "put" else {}),
        )
        assert response.status_code == 404  # No employee identity or foreign tenant.


def test_invalid_settings_are_atomic(
    demo: tuple, client: TestClient, db: Session
) -> None:
    url, people, auth = demo
    org_id = people["E02"].org_id
    foreign = db.exec(select(Department).where(Department.org_id != org_id)).first()
    # Use a syntactically valid foreign id even if that test tenant has no departments.
    foreign_id = str(foreign.id if foreign else people["E02"].id)
    before = client.get(f"{url}/drafting-settings", headers=auth["admin"]).json()
    response = client.put(
        f"{url}/drafting-settings",
        headers=auth["admin"],
        json={
            "excluded_capability_keys": ["unknown-key"],
            "disabled_department_ids": [foreign_id],
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["unknown_capability_keys"] == ["unknown-key"]
    assert response.json()["detail"]["unknown_department_ids"] == [foreign_id]
    assert (
        client.get(f"{url}/drafting-settings", headers=auth["admin"]).json() == before
    )
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


def test_scope_hierarchy_and_bounded_queries(tenant: SetupTenant, db: Session) -> None:
    root = Department(org_id=tenant.org_id, name="上级")
    db.add(root)
    db.flush()
    closed = Department(
        org_id=tenant.org_id,
        name="关闭",
        parent_department_id=root.id,
        auto_draft_disabled=True,
    )
    sibling = Department(
        org_id=tenant.org_id, name="兄弟", parent_department_id=root.id
    )
    db.add_all([closed, sibling])
    db.flush()
    child = Department(
        org_id=tenant.org_id, name="下级", parent_department_id=closed.id
    )
    db.add(child)
    db.flush()
    people = [
        Employee(org_id=tenant.org_id, display_name="合成员工", department_id=d.id)
        for d in (root, closed, child, sibling)
    ]
    opted = Employee(
        org_id=tenant.org_id, display_name="自主退出", auto_draft_opt_out=True
    )
    db.add_all([*people, opted])
    db.commit()
    assert drafting_scope(db, tenant.org_id).excluded_employee_ids == {
        people[1].id,
        people[2].id,
        opted.id,
    }
    sibling.parent_department_id = closed.id
    db.add(sibling)
    db.commit()
    counts = []
    statements = []

    def count(*args: object) -> None:
        statements.append(args[2])

    for size in (0, 100):
        db.add_all(
            Employee(
                org_id=tenant.org_id, display_name="合成扩容", department_id=child.id
            )
            for _ in range(size)
        )
        db.commit()
        statements.clear()
        db.expire_all()
        org_id = tenant.org_id
        event.listen(engine, "before_cursor_execute", count)
        try:
            scope = drafting_scope(db, org_id)
        finally:
            event.remove(engine, "before_cursor_execute", count)
        counts.append(len(statements))
        assert (
            people[0].id not in scope.excluded_employee_ids
            and people[3].id in scope.excluded_employee_ids
        )
        assert len(scope.excluded_employee_ids) == 4 + size
    assert counts == [4, 4]
    foreign = db.exec(
        select(Employee).where(
            Employee.org_id != tenant.org_id, Employee.employee_code == "FOREIGN"
        )
    ).one()
    other_department = Department(org_id=foreign.org_id, name="关闭")
    db.add(other_department)
    db.flush()
    foreign.department_id = other_department.id
    db.add(foreign)
    db.commit()
    assert not drafting_scope(db, foreign.org_id).excluded_employee_ids


def test_roster_preserves_preferences(
    tenant: SetupTenant, client: TestClient, db: Session
) -> None:
    client.headers.update(tenant.auth["admin"])
    data = "工号,姓名,部门\nSYN,合成员工,测试部门\n".encode()
    assert apply(client, tenant, upload(client, tenant, data)).status_code == 200
    employee = db.exec(select(Employee).where(Employee.org_id == tenant.org_id)).one()
    department = db.get(Department, employee.department_id)
    employee.auto_draft_opt_out, department.auto_draft_disabled = True, True
    db.add_all([employee, department])
    db.commit()
    assert apply(client, tenant, upload(client, tenant, data)).status_code == 200
    db.refresh(employee)
    db.refresh(department)
    assert employee.auto_draft_opt_out and department.auto_draft_disabled


def test_migration_roundtrip(migration_engine: Engine) -> None:
    migrate(migration_engine, "downgrade", "f69a901bc002")
    assert "drafting_setting" not in inspect(migration_engine).get_table_names()
    migrate(migration_engine, "upgrade", "head")
    assert "drafting_setting" in inspect(migration_engine).get_table_names()
    assert "auto_draft_disabled" in {
        c["name"] for c in inspect(migration_engine).get_columns("department")
    }
    assert "auto_draft_opt_out" in {
        c["name"] for c in inspect(migration_engine).get_columns("employee")
    }
