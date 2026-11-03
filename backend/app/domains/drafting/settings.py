from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict
from sqlmodel import Session, col, select

from app.authz.principal import Principal
from app.domains.audit.service import record_audit
from app.domains.base import utc_now
from app.domains.capability.models import (
    CapabilityDefinition,
    CapabilitySchemaVersion,
    SchemaStatus,
)
from app.domains.drafting.models import DraftingSetting
from app.domains.org.models import Department, Employee, Organization
from app.domains.org.service import EmployeeNotFound, managed_scope


class DraftingSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    excluded_capability_keys: list[str]
    disabled_department_ids: list[UUID]


class DraftingSettingsPublic(DraftingSettingsUpdate):
    excluded_employee_count: int
    opted_out_count: int
    updated_at: datetime | None


class AutoDraftUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class AutoDraftPublic(AutoDraftUpdate):
    department_disabled: bool


class InvalidDraftingSettings(HTTPException):
    def __init__(self, keys: set[str], departments: set[UUID]) -> None:
        super().__init__(
            422,
            {
                "message": "不认识的能力 key 或部门："
                + "、".join(sorted(keys) + sorted(map(str, departments))),
                "unknown_capability_keys": sorted(keys),
                "unknown_department_ids": sorted(map(str, departments)),
            },
        )


@dataclass(frozen=True)
class DraftingScope:
    excluded_capability_keys: frozenset[str]
    excluded_employee_ids: frozenset[UUID]


def disabled_departments(session: Session, org_id: UUID) -> frozenset[UUID]:
    return frozenset(
        session.exec(
            select(Department.id).where(
                Department.org_id == org_id,
                col(Department.auto_draft_disabled).is_(True),
            )
        ).all()
    )


def drafting_scope(session: Session, org_id: UUID) -> DraftingScope:
    setting = session.get(DraftingSetting, org_id)
    departments = managed_scope(session, org_id, disabled_departments(session, org_id))
    employees = session.exec(
        select(Employee.id).where(
            Employee.org_id == org_id,
            (col(Employee.auto_draft_opt_out).is_(True))
            | col(Employee.department_id).in_(departments),
        )
    ).all()
    return DraftingScope(
        frozenset(setting.excluded_capability_keys if setting else []),
        frozenset(employees),
    )


def read_settings(session: Session, org_id: UUID) -> DraftingSettingsPublic:
    setting = session.get(DraftingSetting, org_id)
    scope = drafting_scope(session, org_id)
    opted_out = session.exec(
        select(Employee.id).where(
            Employee.org_id == org_id,
            col(Employee.auto_draft_opt_out).is_(True),
        )
    ).all()
    return DraftingSettingsPublic(
        excluded_capability_keys=sorted(scope.excluded_capability_keys),
        disabled_department_ids=sorted(disabled_departments(session, org_id)),
        excluded_employee_count=len(scope.excluded_employee_ids),
        opted_out_count=len(opted_out),
        updated_at=setting.updated_at if setting else None,
    )


def save_settings(
    session: Session, principal: Principal, body: DraftingSettingsUpdate
) -> DraftingSettingsPublic:
    session.exec(
        select(Organization)
        .where(Organization.id == principal.org_id)
        .with_for_update()
    ).one()
    keys, department_ids = (
        set(body.excluded_capability_keys),
        set(body.disabled_department_ids),
    )
    known_keys = set(
        session.exec(
            select(CapabilityDefinition.key)
            .join(
                CapabilitySchemaVersion,
                col(CapabilityDefinition.schema_version_id)
                == CapabilitySchemaVersion.id,
            )
            .where(
                CapabilityDefinition.org_id == principal.org_id,
                CapabilitySchemaVersion.org_id == principal.org_id,
                CapabilitySchemaVersion.status == SchemaStatus.ACTIVE,
            )
        ).all()
    )
    departments = session.exec(
        select(Department).where(Department.org_id == principal.org_id)
    ).all()
    unknown_keys = keys - known_keys
    unknown_departments = department_ids - {d.id for d in departments}
    if unknown_keys or unknown_departments:
        raise InvalidDraftingSettings(unknown_keys, unknown_departments)
    setting = session.get(DraftingSetting, principal.org_id)
    old_keys = set(setting.excluded_capability_keys if setting else [])
    old_departments = {d.id for d in departments if d.auto_draft_disabled}
    if keys != old_keys or department_ids != old_departments:
        assert principal.user_id is not None
        setting = setting or DraftingSetting(
            org_id=principal.org_id, updated_by_user_id=principal.user_id
        )
        setting.excluded_capability_keys = sorted(keys)
        setting.updated_by_user_id = principal.user_id
        setting.updated_at = utc_now()
        session.add(setting)
        for department in departments:
            department.auto_draft_disabled = department.id in department_ids
            session.add(department)
        record_audit(
            session,
            org_id=principal.org_id,
            actor_type=principal.actor_type,
            actor_id=str(principal.user_id),
            action="drafting_settings.updated",
            target_type="drafting_setting",
            target_id=str(principal.org_id),
            details={
                "added_capability_keys": [*sorted(keys - old_keys)],
                "removed_capability_keys": [*sorted(old_keys - keys)],
                "added_department_ids": [
                    str(d) for d in sorted(department_ids - old_departments)
                ],
                "removed_department_ids": [
                    str(d) for d in sorted(old_departments - department_ids)
                ],
            },
        )
    session.commit()
    return read_settings(session, principal.org_id)


def own_employee(
    session: Session, principal: Principal, *, lock: bool = False
) -> Employee:
    query = select(Employee).where(
        Employee.org_id == principal.org_id, Employee.id == principal.employee_id
    )
    employee = session.exec(query.with_for_update() if lock else query).first()
    if employee is None:
        raise EmployeeNotFound()
    return employee


def read_auto_draft(session: Session, principal: Principal) -> AutoDraftPublic:
    employee = own_employee(session, principal)
    departments = managed_scope(
        session, principal.org_id, disabled_departments(session, principal.org_id)
    )
    return AutoDraftPublic(
        enabled=not employee.auto_draft_opt_out,
        department_disabled=employee.department_id in departments,
    )


def save_auto_draft(
    session: Session, principal: Principal, body: AutoDraftUpdate
) -> AutoDraftPublic:
    employee = own_employee(session, principal, lock=True)
    if employee.auto_draft_opt_out != (not body.enabled):
        employee.auto_draft_opt_out = not body.enabled
        session.add(employee)
        record_audit(
            session,
            org_id=principal.org_id,
            actor_type=principal.actor_type,
            actor_id=str(principal.user_id),
            action="employee.auto_draft_changed",
            target_type="employee",
            target_id=str(employee.id),
            details={"enabled": body.enabled},
        )
    session.commit()
    return read_auto_draft(session, principal)
