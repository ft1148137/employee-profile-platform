from uuid import UUID

from fastapi import HTTPException
from pydantic import JsonValue
from sqlmodel import Session, select

from app.authz.deps import OrgAdminRequired
from app.authz.principal import Principal
from app.domains.audit.service import record_audit
from app.domains.base import utc_now
from app.domains.capacity.models import CapacitySetting
from app.domains.capacity.schemas import CapacitySettingsInput, Via
from app.domains.org.models import Organization


class CapacityNotFound(HTTPException):
    def __init__(self, detail: str = "未找到该员工或申请") -> None:
        super().__init__(404, detail)


class CapacityForbidden(HTTPException):
    def __init__(self) -> None:
        super().__init__(403, "没有权限执行此操作")


class CapacityConflict(HTTPException):
    def __init__(self) -> None:
        super().__init__(409, "已有待处理申请或申请已处理")


def read_settings(principal: Principal, session: Session) -> CapacitySettingsInput:
    row = session.exec(
        select(CapacitySetting).where(CapacitySetting.org_id == principal.org_id)
    ).one_or_none()
    return CapacitySettingsInput.model_validate(row) if row else CapacitySettingsInput()


def require_enabled(principal: Principal, session: Session) -> None:
    row = session.exec(
        select(CapacitySetting)
        .where(CapacitySetting.org_id == principal.org_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if row is None or not row.enabled:
        raise CapacityNotFound("需求与容量模块未开启")


def audit(
    principal: Principal,
    session: Session,
    action: str,
    target_type: str,
    target_id: UUID,
    via: Via,
    client_name: str | None,
    details: dict[str, JsonValue],
) -> None:
    record_audit(
        session,
        org_id=principal.org_id,
        actor_type=principal.actor_type,
        actor_id=str(principal.user_id),
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        details={**details, "via": via, "client_name": client_name},
    )


def save_settings(
    principal: Principal,
    session: Session,
    body: CapacitySettingsInput,
    *,
    via: Via = "web",
    client_name: str | None = None,
) -> CapacitySettingsInput:
    if not principal.is_org_admin or principal.user_id is None:
        raise OrgAdminRequired()
    session.exec(
        select(Organization)
        .where(Organization.id == principal.org_id)
        .with_for_update()
    ).one()
    row = session.exec(
        select(CapacitySetting)
        .where(CapacitySetting.org_id == principal.org_id)
        .with_for_update()
    ).one_or_none()
    if row is None:
        row = CapacitySetting(
            org_id=principal.org_id, updated_by_user_id=principal.user_id
        )
    row.enabled, row.requester_policy = body.enabled, body.requester_policy
    row.updated_at, row.updated_by_user_id = utc_now(), principal.user_id
    session.add(row)
    audit(
        principal,
        session,
        "capacity.settings_updated",
        "capacity_setting",
        row.id,
        via,
        client_name,
        body.model_dump(mode="json"),
    )
    return body
