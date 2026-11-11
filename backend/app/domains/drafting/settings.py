from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict
from sqlmodel import Session, col, select

from app.authz.principal import Principal
from app.domains.audit.service import record_audit
from app.domains.base import utc_now
from app.domains.drafting.models import DraftingSetting
from app.domains.org.models import Employee, Organization
from app.domains.org.service import EmployeeNotFound


class DraftingSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class DraftingSettingsPublic(DraftingSettingsUpdate):
    pass


class AutoDraftUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class AutoDraftPublic(AutoDraftUpdate):
    org_disabled: bool


class AutoDraftDisabled(HTTPException):
    def __init__(self) -> None:
        super().__init__(422, "自动起草未打开，请先在「数据与 AI → 模型」里打开")


@dataclass(frozen=True)
class DraftingScope:
    excluded_employee_ids: frozenset[UUID]


def drafting_scope(session: Session, org_id: UUID) -> DraftingScope:
    return DraftingScope(
        frozenset(
            session.exec(
                select(Employee.id).where(
                    Employee.org_id == org_id,
                    col(Employee.auto_draft_opt_out).is_(True),
                )
            ).all()
        )
    )


def read_settings(session: Session, org_id: UUID) -> DraftingSettingsPublic:
    setting = session.get(DraftingSetting, org_id, populate_existing=True)
    return DraftingSettingsPublic(enabled=bool(setting and setting.auto_draft_enabled))


def require_auto_drafting(session: Session, org_id: UUID) -> None:
    if not read_settings(session, org_id).enabled:
        raise AutoDraftDisabled()


def save_settings(
    session: Session,
    principal: Principal,
    body: DraftingSettingsUpdate,
    *,
    commit: bool = True,
) -> DraftingSettingsPublic:
    session.exec(
        select(Organization)
        .where(Organization.id == principal.org_id)
        .with_for_update()
    ).one()
    before = read_settings(session, principal.org_id).enabled
    if body.enabled != before:
        assert principal.user_id is not None
        setting = session.get(DraftingSetting, principal.org_id) or DraftingSetting(
            org_id=principal.org_id, updated_by_user_id=principal.user_id
        )
        setting.auto_draft_enabled = body.enabled
        setting.updated_by_user_id = principal.user_id
        setting.updated_at = utc_now()
        session.add(setting)
        record_audit(
            session,
            org_id=principal.org_id,
            actor_type=principal.actor_type,
            actor_id=str(principal.user_id),
            action="drafting_settings.updated",
            target_type="drafting_setting",
            target_id=str(principal.org_id),
            details={"before": {"enabled": before}, "after": {"enabled": body.enabled}},
        )
    if commit:
        session.commit()
    else:
        session.flush()
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
    return AutoDraftPublic(
        enabled=not employee.auto_draft_opt_out,
        org_disabled=not read_settings(session, principal.org_id).enabled,
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
