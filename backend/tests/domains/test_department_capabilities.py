import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, inspect
from sqlmodel import Session, select

from app.domains.admin_assistant import change_set as c
from app.domains.bot.schemas import Profile as BotProfile
from app.domains.capability.department import (
    DepartmentArgs,
    read_department_capabilities,
    snapshot,
)
from app.domains.capability.models import DepartmentCapability
from app.domains.capability.service import read_editor, save_version
from app.domains.org.models import Department
from app.domains.profile.service import read_profile
from tests.admin_assistant.test_change_set import proposal
from tests.admin_assistant.test_registry import principal
from tests.utils.migrations import migrate


def test_inheritance_profile_and_dictionary_versions(
    db: Session, demo: tuple, client: TestClient
) -> None:
    url, employees, auth = demo
    p = principal(db, demo)
    parent_id = employees["E02"].department_id
    assert parent_id
    child = Department(org_id=p.org_id, name="后端组", parent_department_id=parent_id)
    db.add(child)
    db.flush()
    employees["E02"].department_id = child.id
    db.add(employees["E02"])
    db.commit()
    editor = read_editor(db, p)
    keys = [d.key for d in editor.definitions[:3]]
    assert len(keys) == 3
    assert not read_profile(
        db, p, employees["E02"].id
    ).department_capabilities_configured
    path = url + f"/departments/{parent_id}/capabilities"
    response = client.put(
        path, headers=auth["admin"], json={"capability_keys": keys[:2]}
    )
    assert response.status_code == 200, response.text
    assert response.json()["capability_keys"] == sorted(keys[:2])
    child_path = url + f"/departments/{child.id}/capabilities"
    response = client.put(
        child_path, headers=auth["admin"], json={"capability_keys": keys[1:]}
    )
    assert response.status_code == 200
    data = client.get(child_path, headers=auth["manager"]).json()
    assert data["inherited_capability_keys"] == sorted(keys[:2])
    assert data["capability_keys"] == sorted(keys[1:])
    assert {d["key"] for d in data["capabilities"]} == set(keys)
    profile = read_profile(db, p, employees["E02"].id)
    assert profile.department_capabilities_configured
    assert {i.capability.key for i in profile.items if i.in_department} == set(keys)
    assert len(profile.items) == len(editor.definitions)
    other = read_profile(db, p, employees["E05"].id)
    assert not other.department_capabilities_configured
    assert not any(i.in_department for i in other.items)
    projection = BotProfile.model_validate(profile).model_dump()
    assert "department_capabilities_configured" not in projection
    assert all("in_department" not in i for i in projection["items"])

    # Stable keys survive definition IDs changing; deleted keys remain stored but hidden.
    save_version(db, p, [d for d in editor.definitions if d.key != keys[0]])
    db.commit()
    data = client.get(child_path, headers=auth["admin"]).json()
    assert data["inherited_capability_keys"] == [keys[1]]
    profile = read_profile(db, p, employees["E02"].id)
    assert {i.capability.key for i in profile.items if i.in_department} == set(keys[1:])
    assert snapshot(
        db, p, DepartmentArgs(department_id=parent_id)
    ).capability_keys == sorted(keys[:2])
    save_version(db, p, [d for d in editor.definitions if d.key not in keys])
    db.commit()
    profile = read_profile(db, p, employees["E02"].id)
    assert profile.department_capabilities_configured
    assert not any(i.in_department for i in profile.items)
    assert client.get(child_path, headers=auth["admin"]).json()["capabilities"] == []


@pytest.mark.parametrize("role", ["manager", "employee", "foreign"])
def test_permissions_and_foreign_departments(
    db: Session, demo: tuple, client: TestClient, role: str
) -> None:
    url, employees, auth = demo
    department_id = employees["E02"].department_id
    path = url + f"/departments/{department_id}/capabilities"
    assert (
        client.get(path, headers=auth[role]).status_code
        == {
            "manager": 200,
            "employee": 403,
            "foreign": 404,
        }[role]
    )
    assert client.put(
        path, headers=auth[role], json={"capability_keys": []}
    ).status_code == (404 if role == "foreign" else 403)
    other = url + f"/departments/{employees['E05'].department_id}/capabilities"
    assert client.get(other, headers=auth["manager"]).status_code == 403
    p = principal(db, demo)
    foreign_department = db.exec(
        select(Department).where(Department.org_id != p.org_id)
    ).first()
    assert foreign_department
    foreign_path = url + f"/departments/{foreign_department.id}/capabilities"
    for method in ("GET", "PUT"):
        assert (
            client.request(
                method,
                foreign_path,
                headers=auth["admin"],
                **({"json": {"capability_keys": []}} if method == "PUT" else {}),
            ).status_code
            == 404
        )


def test_validation_audit_confirmation_and_undo(
    db: Session, demo: tuple, client: TestClient
) -> None:
    from app.domains.audit.models import AuditEvent

    p = principal(db, demo)
    department_id = demo[1]["E02"].department_id
    assert department_id
    path = demo[0] + f"/departments/{department_id}/capabilities"
    key = read_editor(db, p).definitions[0].key
    assert (
        client.put(
            path, headers=demo[2]["admin"], json={"capability_keys": ["missing"]}
        ).status_code
        == 422
    )
    assert (
        client.put(
            path, headers=demo[2]["admin"], json={"capability_keys": [key, key]}
        ).status_code
        == 200
    )
    args = DepartmentArgs(department_id=department_id)
    row = proposal(
        db,
        demo,
        items=[
            {
                "function": "set_department_capabilities",
                "args": {"department_id": str(department_id), "capability_keys": []},
            }
        ],
    )
    assert snapshot(db, p, args).capability_keys == [key]
    card_path = demo[0] + f"/admin-assistant/change-sets/{row.id}"
    card = client.get(card_path, headers=demo[2]["admin"]).json()
    assert card["items"][0]["before"]["capability_keys"] == [key]
    assert card["items"][0]["after"]["capability_keys"] == []
    assert (
        client.post(
            card_path + "/apply",
            headers=demo[2]["admin"],
            json={"revision": row.revision},
        ).status_code
        == 200
    )
    assert read_department_capabilities(db, p, args).capability_keys == []
    assert not read_profile(db, p, demo[1]["E02"].id).department_capabilities_configured
    c.undo(db, p, row.id)
    assert snapshot(db, p, args).capability_keys == [key]
    events = db.exec(
        select(AuditEvent)
        .where(
            AuditEvent.org_id == p.org_id,
            AuditEvent.action == "department.capabilities_updated",
        )
        .order_by(AuditEvent.created_at, AuditEvent.id)
    ).all()
    assert len(events) == 3
    assert events[0].details == {"before": [], "after": [key]}
    assert events[1].details["before"] == [key]
    assert events[1].details["after"] == []
    assert events[1].details["via"] == "admin_assistant"
    assert events[2].details["undo_of_id"] == str(row.id)


def test_stale_confirmation(db: Session, demo: tuple, client: TestClient) -> None:
    p = principal(db, demo)
    department_id = demo[1]["E02"].department_id
    keys = [d.key for d in read_editor(db, p).definitions[:2]]
    row = proposal(
        db,
        demo,
        items=[
            {
                "function": "set_department_capabilities",
                "args": {
                    "department_id": str(department_id),
                    "capability_keys": [keys[0]],
                },
            }
        ],
    )
    client.put(
        demo[0] + f"/departments/{department_id}/capabilities",
        headers=demo[2]["admin"],
        json={"capability_keys": [keys[1]]},
    )
    with pytest.raises(c.ChangeSetError, match="内容已变化"):
        c.apply(db, p, row.id, row.revision)


def test_migration_roundtrip(migration_engine: Engine) -> None:
    migrate(migration_engine, "downgrade", "b317001bc001")
    assert "department_capability" not in inspect(migration_engine).get_table_names()
    migrate(migration_engine, "upgrade", "head")
    inspector = inspect(migration_engine)
    assert inspector.get_pk_constraint("department_capability")[
        "constrained_columns"
    ] == ["org_id", "department_id", "capability_key"]
    assert any(
        f["referred_table"] == "department" and f["options"]["ondelete"] == "CASCADE"
        for f in inspector.get_foreign_keys("department_capability")
    )
    assert DepartmentCapability.__tablename__ in inspector.get_table_names()
    migrate(migration_engine, "downgrade", "b317001bc001")
    migrate(migration_engine, "upgrade", "head")
