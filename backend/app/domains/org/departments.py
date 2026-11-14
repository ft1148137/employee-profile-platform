"""Administrator department edits shared by the console and confirmation cards."""

from collections import defaultdict
from typing import Annotated
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, RootModel, StringConstraints
from sqlmodel import Session, col, select

from app.authz.principal import Principal
from app.domains.account.service import lock_org, require_admin
from app.domains.audit.service import record_audit
from app.domains.capability.department import DepartmentArgs
from app.domains.org.models import (
    Department,
    Employee,
    EmploymentStatus,
    OrgMembership,
    Role,
    RoleGrant,
)
from app.domains.org.paths import department_paths
from app.domains.org.schemas import DepartmentPublic
from app.domains.org.service import managed_scope
from app.models import Message, User

Name = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
]


class DepartmentError(HTTPException):
    pass


class DepartmentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name | None = None
    parent_department_id: UUID | None = None


class RenameDepartment(DepartmentArgs):
    name: Name


class MoveDepartment(DepartmentArgs):
    parent_department_id: UUID | None


def department(db: Session, p: Principal, id: UUID) -> Department:
    require_admin(p)
    row = db.exec(
        select(Department).where(Department.org_id == p.org_id, Department.id == id)
    ).first()
    if row is None:
        raise DepartmentError(404, "部门不存在")
    return row


def validate_update(
    db: Session, p: Principal, id: UUID, body: DepartmentUpdate
) -> Department:
    row = department(db, p, id)
    if not body.model_fields_set or (
        "name" in body.model_fields_set and body.name is None
    ):
        raise DepartmentError(422, "请填写部门名称或上级部门，名称不能为空")
    parent = (
        body.parent_department_id
        if "parent_department_id" in body.model_fields_set
        else row.parent_department_id
    )
    if parent is not None:
        department(db, p, parent)
        if parent in managed_scope(db, p.org_id, frozenset({id})):
            raise DepartmentError(422, "不能移到自己或下级部门，部门层级不能成环")
    sibling = db.exec(
        select(Department.id).where(
            Department.org_id == p.org_id,
            Department.id != id,
            Department.parent_department_id == parent,
            Department.name == (body.name if body.name is not None else row.name),
        )
    ).first()
    if sibling:
        raise DepartmentError(409, "同一上级下已有同名部门，请换一个名称")
    return row


def update_department(
    db: Session, p: Principal, id: UUID, body: DepartmentUpdate, *, commit: bool = True
) -> DepartmentPublic:
    org = lock_org(db, p)
    row = validate_update(db, p, id, body)
    before = DepartmentPublic.model_validate(row)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    db.add(row)
    after = DepartmentPublic.model_validate(row)
    for field, action in [
        ("name", "department.renamed"),
        ("parent_department_id", "department.moved"),
    ]:
        if getattr(before, field) != getattr(after, field):
            record_audit(
                db,
                org_id=p.org_id,
                actor_type=p.actor_type,
                actor_id=str(p.user_id),
                action=action,
                target_type="department",
                target_id=str(id),
                details={
                    "before": before.model_dump(mode="json"),
                    "after": after.model_dump(mode="json"),
                },
            )
    if before != after:
        org.roster_revision += 1
        db.add(org)
    db.commit() if commit else db.flush()
    return after


def deletable(db: Session, p: Principal, id: UUID) -> Department:
    row = department(db, p, id)
    if db.exec(
        select(Employee.id).where(
            Employee.org_id == p.org_id, Employee.department_id == id
        )
    ).first():
        raise DepartmentError(409, "部门还有员工（包括已离职员工），不能删除")
    if db.exec(
        select(Department.id).where(
            Department.org_id == p.org_id, Department.parent_department_id == id
        )
    ).first():
        raise DepartmentError(409, "部门还有下级部门，不能删除")
    managers = db.exec(
        select(User)
        .join(OrgMembership, col(OrgMembership.user_id) == User.id)
        .join(RoleGrant, col(RoleGrant.membership_id) == OrgMembership.id)
        .where(
            OrgMembership.org_id == p.org_id,
            RoleGrant.org_id == p.org_id,
            RoleGrant.department_id == id,
        )
        .order_by(col(User.id))
    ).all()
    if managers:
        raise DepartmentError(
            409,
            f"先撤销 {'、'.join(u.full_name or u.username for u in managers)} 的部门经理授权",
        )
    return row


def delete_department(
    db: Session, p: Principal, args: DepartmentArgs, *, commit: bool = True
) -> Message:
    org = lock_org(db, p)
    row = deletable(db, p, args.department_id)
    before = DepartmentPublic.model_validate(row)
    db.delete(row)
    org.roster_revision += 1
    db.add(org)
    record_audit(
        db,
        org_id=p.org_id,
        actor_type=p.actor_type,
        actor_id=str(p.user_id),
        action="department.deleted",
        target_type="department",
        target_id=str(row.id),
        details={"before": before.model_dump(mode="json")},
    )
    db.commit() if commit else db.flush()
    return Message(message="空部门已删除")


def move_impact(db: Session, p: Principal, args: MoveDepartment) -> BaseModel:
    row = validate_update(
        db,
        p,
        args.department_id,
        DepartmentUpdate(parent_department_id=args.parent_department_id),
    )
    departments = {
        d.id: d
        for d in db.exec(select(Department).where(Department.org_id == p.org_id)).all()
    }
    paths = department_paths(list(departments.values()))
    subtree = managed_scope(db, p.org_id, frozenset({row.id}))
    employees = db.exec(
        select(Employee.department_id).where(
            Employee.org_id == p.org_id,
            Employee.employment_status == EmploymentStatus.ACTIVE,
        )
    ).all()
    count = sum(id in subtree for id in employees)
    grants: dict[UUID, set[UUID]] = defaultdict(set)
    labels: dict[UUID, str] = {}
    for grant, membership, user in db.exec(
        select(RoleGrant, OrgMembership, User)
        .join(OrgMembership, col(OrgMembership.id) == RoleGrant.membership_id)
        .join(User, col(User.id) == OrgMembership.user_id)
        .where(
            RoleGrant.org_id == p.org_id,
            OrgMembership.org_id == p.org_id,
            RoleGrant.role == Role.DEPT_MANAGER,
        )
        .order_by(col(User.id), col(RoleGrant.id))
    ).all():
        assert grant.department_id is not None
        grants[membership.id].add(grant.department_id)
        labels[membership.id] = user.full_name or user.username
    target = (
        paths[args.parent_department_id]
        if args.parent_department_id
        else "第一层（无上级）"
    )
    changes = [
        f"将「{paths[row.id]}」移动到 {target}；本部门及下级共 {count} 名在职员工"
    ]
    for member, roots in grants.items():
        before = managed_scope(db, p.org_id, frozenset(roots))
        # Moving changes ancestor-derived scope; direct grants inside the subtree survive.
        after = (before - subtree) | managed_scope(
            db, p.org_id, frozenset(roots & subtree)
        )
        if args.parent_department_id in before - subtree:
            after |= subtree
        label = f"{'、'.join(sorted(paths[id] for id in roots))} 的部门经理 {labels[member]}"
        for scope, text in [
            (after - before, "将能查看和审批"),
            (before - after, "不再管辖"),
        ]:
            if scope:
                changes.append(
                    f"{label} {text}这些部门的 {sum(id in scope for id in employees)} 名在职员工"
                )
    if len(changes) == 1:
        changes.append("部门经理管辖范围不变")
    changes.append("可撤销；审批仍遵守不能批准自己的请求等规则")
    return RootModel({"count": count, "text": "；".join(changes)})


def snapshot(db: Session, p: Principal, args: DepartmentArgs) -> BaseModel:
    row = department(db, p, args.department_id)
    state = {
        "department_id": str(row.id),
        "name": row.name,
        "parent_department_id": str(row.parent_department_id)
        if row.parent_department_id
        else None,
    }
    if isinstance(args, MoveDepartment):
        return RootModel(
            {**state, "impact": move_impact(db, p, args).model_dump(mode="json")}
        )
    return RootModel(state)


def rename_impact(db: Session, p: Principal, args: RenameDepartment) -> BaseModel:
    row = validate_update(db, p, args.department_id, DepartmentUpdate(name=args.name))
    return RootModel(
        {"count": 0, "text": f"将「{row.name}」改名为「{args.name}」；可撤销"}
    )


def delete_impact(db: Session, p: Principal, args: DepartmentArgs) -> BaseModel:
    row = deletable(db, p, args.department_id)
    return RootModel(
        {
            "count": 0,
            "text": f"删除空部门「{row.name}」及其对应能力设置；不可撤销，需要再次确认",
        }
    )
