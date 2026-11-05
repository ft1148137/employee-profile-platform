from calendar import monthrange
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, select

from app.authz.principal import Principal
from app.domains.audit.service import record_audit
from app.domains.base import utc_now
from app.domains.drafting.models import UpdateSetting
from app.domains.org.models import Organization
from app.domains.source.schedule import add_period


class UpdateSettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reminder_schedule: Literal["quarterly", "yearly", "off"] = "quarterly"
    stale_after_months: int = Field(default=24, ge=6, le=120)


class UpdateSettingsPublic(UpdateSettingsInput):
    next_reminder_at: datetime | None
    updated_at: datetime | None


def months_before(now: datetime, months: int) -> datetime:
    year, month = divmod(now.year * 12 + now.month - 1 - months, 12)
    month += 1
    return now.replace(
        year=year, month=month, day=min(now.day, monthrange(year, month)[1])
    )


def read_update_settings(session: Session, org_id: UUID) -> UpdateSettingsPublic:
    setting = session.get(UpdateSetting, org_id)
    if setting:
        return UpdateSettingsPublic.model_validate(setting, from_attributes=True)
    org = session.get(Organization, org_id)
    assert org is not None
    return UpdateSettingsPublic(
        next_reminder_at=add_period(org.created_at, "quarterly"), updated_at=None
    )


def save_update_settings(
    session: Session, principal: Principal, body: UpdateSettingsInput
) -> UpdateSettingsPublic:
    session.exec(
        select(Organization)
        .where(Organization.id == principal.org_id)
        .with_for_update()
    ).one()
    old = read_update_settings(session, principal.org_id)
    if body.model_dump() != old.model_dump(
        include=set(UpdateSettingsInput.model_fields)
    ):
        setting = session.get(UpdateSetting, principal.org_id) or UpdateSetting(
            org_id=principal.org_id
        )
        setting.stale_after_months = body.stale_after_months
        setting.reminder_schedule = body.reminder_schedule
        setting.next_reminder_at = (
            None
            if body.reminder_schedule == "off"
            else add_period(utc_now(), body.reminder_schedule)
            if body.reminder_schedule != old.reminder_schedule
            else old.next_reminder_at
        )
        setting.updated_by_user_id = principal.user_id
        setting.updated_at = utc_now()
        session.add(setting)
        record_audit(
            session,
            org_id=principal.org_id,
            actor_type=principal.actor_type,
            actor_id=str(principal.user_id),
            action="update_settings.updated",
            target_type="update_setting",
            target_id=str(principal.org_id),
            details={
                "before": old.model_dump(mode="json"),
                "after": body.model_dump(mode="json"),
            },
        )
    session.commit()
    return read_update_settings(session, principal.org_id)
