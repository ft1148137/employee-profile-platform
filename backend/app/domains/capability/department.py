"""Department capabilities use stable dictionary keys and inherit from ancestors."""

from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict
from sqlmodel import Session, col, delete, select
from sqlmodel.sql.expression import SelectOfScalar

from app.authz.principal import Principal
from app.domains.account.service import lock_org
from app.domains.audit.service import record_audit
from app.domains.capability.models import (
    CapabilityDefinition,
    CapabilitySchemaVersion,
    DepartmentCapability,
    SchemaStatus,
)
from app.domains.org.models import Department


class DepartmentCapabilityError(HTTPException):
    pass


class DepartmentCapabilitiesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capability_keys: list[str]


class DepartmentArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    department_id: UUID


class SetDepartmentCapabilities(DepartmentCapabilitiesInput):
    department_id: UUID


class DepartmentCapabilityPublic(BaseModel):
    key: str
    name: str
    category: str | None


class DepartmentCapabilitiesPublic(DepartmentCapabilitiesInput):
    inherited_capability_keys: list[str]
    capabilities: list[DepartmentCapabilityPublic]


def visible_department(db: Session, p: Principal, id: UUID) -> Department:
    department = db.exec(
        select(Department).where(Department.org_id == p.org_id, Department.id == id)
    ).first()
    if department is None:
        raise DepartmentCapabilityError(404, "部门不存在")
    if not p.is_org_admin and id not in p.managed_department_ids:
        raise DepartmentCapabilityError(403, "没有权限查看该部门的能力")
    return department


def department_assignments(
    org_id: UUID, department_id: UUID | None
) -> SelectOfScalar[DepartmentCapability]:
    ancestors = (
        select(Department.id, Department.parent_department_id)
        .where(Department.org_id == org_id, Department.id == department_id)
        .cte(recursive=True)
    )
    # UNION also terminates safely if an imported hierarchy contains a cycle.
    ancestors = ancestors.union(
        select(Department.id, Department.parent_department_id)
        .join(ancestors, col(Department.id) == ancestors.c.parent_department_id)
        .where(Department.org_id == org_id)
    )
    return select(DepartmentCapability).where(
        DepartmentCapability.org_id == org_id,
        col(DepartmentCapability.department_id).in_(select(ancestors.c.id)),
    )


def department_keys(
    db: Session, org_id: UUID, department_id: UUID | None
) -> tuple[set[str], set[str]]:
    rows = db.exec(department_assignments(org_id, department_id)).all()
    return (
        {r.capability_key for r in rows if r.department_id == department_id},
        {r.capability_key for r in rows if r.department_id != department_id},
    )


def read_department_capabilities(
    db: Session, p: Principal, args: DepartmentArgs
) -> DepartmentCapabilitiesPublic:
    visible_department(db, p, args.department_id)
    own, inherited = department_keys(db, p.org_id, args.department_id)
    definitions = db.exec(
        select(CapabilityDefinition)
        .join(
            CapabilitySchemaVersion,
            (col(CapabilitySchemaVersion.id) == CapabilityDefinition.schema_version_id)
            & (col(CapabilitySchemaVersion.org_id) == CapabilityDefinition.org_id),
        )
        .where(
            CapabilityDefinition.org_id == p.org_id,
            CapabilitySchemaVersion.status == SchemaStatus.ACTIVE,
            col(CapabilityDefinition.key).in_(own | inherited),
        )
        .order_by(col(CapabilityDefinition.position), col(CapabilityDefinition.key))
    ).all()
    active = {d.key for d in definitions}
    return DepartmentCapabilitiesPublic(
        capability_keys=sorted(own & active),
        inherited_capability_keys=sorted(inherited & active),
        capabilities=[
            DepartmentCapabilityPublic(key=d.key, name=d.name, category=d.category)
            for d in definitions
        ],
    )


def snapshot(
    db: Session, p: Principal, args: DepartmentArgs | SetDepartmentCapabilities
) -> SetDepartmentCapabilities:
    visible_department(db, p, args.department_id)
    own, _ = department_keys(db, p.org_id, args.department_id)
    return SetDepartmentCapabilities(
        department_id=args.department_id, capability_keys=sorted(own)
    )


def set_department_capabilities(
    db: Session, p: Principal, args: SetDepartmentCapabilities, *, commit: bool = True
) -> DepartmentCapabilitiesPublic:
    lock_org(db, p)
    before = snapshot(db, p, args).capability_keys
    after = sorted(set(args.capability_keys))
    active = db.exec(
        select(CapabilityDefinition.key)
        .join(
            CapabilitySchemaVersion,
            (col(CapabilitySchemaVersion.id) == CapabilityDefinition.schema_version_id)
            & (col(CapabilitySchemaVersion.org_id) == CapabilityDefinition.org_id),
        )
        .where(
            CapabilityDefinition.org_id == p.org_id,
            CapabilitySchemaVersion.status == SchemaStatus.ACTIVE,
            col(CapabilityDefinition.key).in_(after),
        )
    ).all()
    if set(after) - set(active):
        raise DepartmentCapabilityError(422, "能力必须在当前启用的词典中")
    if before != after:
        db.exec(
            delete(DepartmentCapability).where(
                col(DepartmentCapability.org_id) == p.org_id,
                col(DepartmentCapability.department_id) == args.department_id,
            )
        )
        assert p.user_id is not None
        db.add_all(
            DepartmentCapability(
                org_id=p.org_id,
                department_id=args.department_id,
                capability_key=key,
                created_by_user_id=p.user_id,
            )
            for key in after
        )
        record_audit(
            db,
            org_id=p.org_id,
            actor_type=p.actor_type,
            actor_id=str(p.user_id),
            action="department.capabilities_updated",
            target_type="department",
            target_id=str(args.department_id),
            details={"before": [*before], "after": [*after]},
        )
    if commit:
        db.commit()
    else:
        db.flush()
    return read_department_capabilities(
        db, p, DepartmentArgs(department_id=args.department_id)
    )
