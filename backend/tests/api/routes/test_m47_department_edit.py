from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.authz.deps import get_org_principal
from app.domains.account.service import AccountError
from app.domains.admin_assistant import change_set as c
from app.domains.audit.models import AuditEvent
from app.domains.capability.models import DepartmentCapability
from app.domains.org import departments as edits
from app.domains.org.models import (
    Department,
    EmploymentStatus,
    OrgMembership,
    Role,
    RoleGrant,
)
from app.models import User
from tests.admin_assistant.test_change_set import proposal
from tests.admin_assistant.test_registry import principal


def empty(
    db: Session, demo: tuple, name: str = "空部门", parent: UUID | None = None
) -> Department:
    row = Department(
        org_id=principal(db, demo).org_id, name=name, parent_department_id=parent
    )
    db.add(row)
    db.commit()
    return row


def grant(db: Session, demo: tuple, department_id: UUID, role: str = "admin") -> None:
    p = principal(db, demo, role)
    member = db.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == p.org_id, OrgMembership.user_id == p.user_id
        )
    ).one()
    db.add(
        RoleGrant(
            org_id=p.org_id,
            membership_id=member.id,
            role=Role.DEPT_MANAGER,
            department_id=department_id,
        )
    )
    db.commit()


def test_rename_department(db: Session, demo: tuple, client: TestClient) -> None:
    p = principal(db, demo)
    department = Department(org_id=p.org_id, name="待改名的空部门")
    db.add(department)
    db.commit()
    response = client.patch(
        demo[0] + f"/departments/{department.id}",
        headers=demo[2]["admin"],
        json={"name": "新部门"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "新部门"
    parent = empty(db, demo, "上级")
    path = demo[0] + f"/departments/{department.id}"
    for target in (str(parent.id), None):
        response = client.patch(
            path, headers=demo[2]["admin"], json={"parent_department_id": target}
        )
        assert response.status_code == 200, response.text
        assert response.json()["parent_department_id"] == target
    db.add(
        DepartmentCapability(
            org_id=p.org_id,
            department_id=department.id,
            capability_key="synthetic",
            created_by_user_id=p.user_id,
        )
    )
    db.commit()
    department_id = department.id
    assert client.delete(path, headers=demo[2]["admin"]).status_code == 200
    db.expire_all()
    assert db.get(Department, department_id) is None
    assert not db.exec(
        select(DepartmentCapability).where(
            DepartmentCapability.org_id == p.org_id,
            DepartmentCapability.department_id == department_id,
        )
    ).all()
    events = db.exec(
        select(AuditEvent)
        .where(
            AuditEvent.org_id == p.org_id, AuditEvent.target_id == str(department_id)
        )
        .order_by(AuditEvent.id)
    ).all()
    assert [e.action for e in events] == [
        "department.renamed",
        "department.moved",
        "department.moved",
        "department.deleted",
    ]
    assert events[-1].details["before"]["name"] == "新部门"


def test_cycles_duplicates_and_cross_tenant(
    db: Session, demo: tuple, client: TestClient
) -> None:
    parent = empty(db, demo)
    child = empty(db, demo, "下级", parent.id)
    sibling = empty(db, demo, "同级")
    target = empty(db, demo, "目标")
    empty(db, demo, parent.name, target.id)
    foreign = db.exec(
        select(Department).where(Department.org_id != parent.org_id)
    ).first()
    assert foreign
    path = demo[0] + f"/departments/{parent.id}"
    for body, code, reason in [
        ({"parent_department_id": str(parent.id)}, 422, "成环"),
        ({"parent_department_id": str(child.id)}, 422, "成环"),
        ({"name": sibling.name}, 409, "同名"),
        ({"parent_department_id": str(target.id)}, 409, "同名"),
        ({"parent_department_id": str(foreign.id)}, 404, "部门不存在"),
        ({"name": None}, 422, "不能为空"),
        ({}, 422, "请填写"),
    ]:
        response = client.patch(path, headers=demo[2]["admin"], json=body)
        assert response.status_code == code, response.text
        assert reason in response.json()["detail"]
    # A combined rename/move validates the final path and writes both events.
    assert (
        client.patch(
            path,
            headers=demo[2]["admin"],
            json={"name": "新名字", "parent_department_id": str(target.id)},
        ).status_code
        == 200
    )
    for method in ("PATCH", "DELETE"):
        response = client.request(
            method,
            demo[0] + f"/departments/{foreign.id}",
            headers=demo[2]["admin"],
            **({"json": {"name": "外部"}} if method == "PATCH" else {}),
        )
        assert response.status_code == 404


@pytest.mark.parametrize("blocker", ["active", "inactive", "child", "grant"])
def test_delete_blockers(
    db: Session, demo: tuple, client: TestClient, blocker: str
) -> None:
    row = empty(db, demo)
    if blocker in ("active", "inactive"):
        employee = demo[1]["E02"]
        employee.department_id = row.id
        employee.employment_status = EmploymentStatus(blocker)
        db.add(employee)
        db.commit()
    elif blocker == "child":
        empty(db, demo, "下级", row.id)
    else:
        grant(db, demo, row.id)
    response = client.delete(
        demo[0] + f"/departments/{row.id}", headers=demo[2]["admin"]
    )
    assert response.status_code == 409
    assert {"active": "员工", "inactive": "已离职", "child": "下级", "grant": "先撤销"}[
        blocker
    ] in response.json()["detail"]
    if blocker == "grant":
        assert "企业管理员" in response.json()["detail"]


@pytest.mark.parametrize("role", ["manager", "employee", "foreign"])
def test_permissions(db: Session, demo: tuple, client: TestClient, role: str) -> None:
    row = empty(db, demo)
    path = demo[0] + f"/departments/{row.id}"
    for method in ("PATCH", "DELETE"):
        response = client.request(
            method,
            path,
            headers=demo[2][role],
            **({"json": {"name": "越权"}} if method == "PATCH" else {}),
        )
        assert response.status_code == (404 if role == "foreign" else 403)
    p = principal(db, demo, "employee")
    with pytest.raises(AccountError, match="需要企业管理员"):
        edits.update_department(db, p, row.id, edits.DepartmentUpdate(name="越权"))
    with pytest.raises(AccountError, match="需要企业管理员"):
        edits.delete_department(db, p, edits.DepartmentArgs(department_id=row.id))


def test_move_scope_and_confirmation(
    db: Session, demo: tuple, client: TestClient
) -> None:
    p = principal(db, demo)
    old_parent = demo[1]["E01"].department_id
    target = empty(db, demo, "新上级")
    row = empty(db, demo, parent=old_parent)
    child = empty(db, demo, "直属授权保留", row.id)
    grant(db, demo, target.id, "employee")
    subject = demo[1]["E03"]
    subject.department_id = child.id
    db.add(subject)
    db.commit()
    args = {"department_id": str(row.id), "parent_department_id": str(target.id)}
    card = proposal(db, demo, items=[{"function": "move_department", "args": args}])
    item = c.public(card).items[0]
    assert item.risk == "high" and item.undoable
    assert item.impact and item.impact.count == 1
    assert (
        "新上级" in item.impact.text
        and "将能查看和审批这些部门的 1 名" in item.impact.text
    )
    assert "不再管辖这些部门的 1 名" in item.impact.text
    path = demo[0] + f"/employees/{subject.id}/profile"
    assert client.get(path, headers=demo[2]["manager"]).status_code == 200
    assert client.get(path, headers=demo[2]["employee"]).status_code == 404
    assert c.apply(db, p, card.id, card.revision).status == "draft"
    assert c.apply(db, p, card.id, card.revision).status == "applied"
    assert client.get(path, headers=demo[2]["manager"]).status_code == 404
    assert client.get(path, headers=demo[2]["employee"]).status_code == 200
    reverse = c.undo(db, p, card.id)
    assert reverse.status == "draft"
    assert c.apply(db, p, reverse.id, reverse.revision).status == "applied"
    assert client.get(path, headers=demo[2]["manager"]).status_code == 200
    # Direct grants inside the moved subtree remain effective.
    manager = db.exec(
        select(User).where(User.email == "manager.platform@demo.example.com")
    ).one()
    member = db.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == p.org_id, OrgMembership.user_id == manager.id
        )
    ).one()
    db.add(
        RoleGrant(
            org_id=p.org_id,
            membership_id=member.id,
            role=Role.DEPT_MANAGER,
            department_id=child.id,
        )
    )
    db.commit()
    edits.update_department(
        db, p, row.id, edits.DepartmentUpdate(parent_department_id=target.id)
    )
    assert child.id in get_org_principal(p.org_id, manager, db).managed_department_ids
    assert row.id not in get_org_principal(p.org_id, manager, db).managed_department_ids


def test_assistant_rename_delete_and_stale(db: Session, demo: tuple) -> None:
    p = principal(db, demo)
    row = empty(db, demo)
    args = {"department_id": str(row.id)}
    card = proposal(
        db,
        demo,
        items=[{"function": "rename_department", "args": {**args, "name": "新名称"}}],
    )
    assert "空部门" in c.public(card).items[0].impact.text
    assert "新名称" in c.public(card).items[0].impact.text
    c.apply(db, p, card.id, card.revision)
    assert c.undo(db, p, card.id).status == "applied"
    db.expire_all()
    assert row.name == "空部门"
    target = empty(db, demo, "新上级")
    move = proposal(
        db,
        demo,
        items=[
            {
                "function": "move_department",
                "args": {**args, "parent_department_id": str(target.id)},
            }
        ],
    )
    employee = demo[1]["E03"]
    employee.department_id = row.id
    db.add(employee)
    db.commit()
    with pytest.raises(c.ChangeSetError, match="内容已变化"):
        c.apply(db, p, move.id, move.revision)
    employee.department_id = None
    db.add(employee)
    db.commit()
    card = proposal(db, demo, items=[{"function": "delete_department", "args": args}])
    item = c.public(card).items[0]
    assert not item.undoable and item.risk == "high"
    assert "不可撤销，需要再次确认" in item.impact.text
    assert c.apply(db, p, card.id, card.revision).status == "draft"
    assert db.get(Department, row.id) is not None
    assert c.apply(db, p, card.id, card.revision).status == "applied"
    with pytest.raises(c.ChangeSetError, match="不能撤销"):
        c.undo(db, p, card.id)
