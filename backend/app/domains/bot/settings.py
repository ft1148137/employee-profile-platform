from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, select

from app.authz.principal import Principal
from app.domains.audit.service import record_audit
from app.domains.bot.models import BotSetting
from app.domains.org.models import Organization


class BotSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_guide: str = Field(default="", max_length=4000)
    retention_days: int = Field(default=0, ge=0, le=30)


def read_settings(db: Session, org_id: UUID) -> BotSettings:
    row = db.get(BotSetting, org_id)
    return (
        BotSettings.model_validate(row, from_attributes=True) if row else BotSettings()
    )


def save_settings(
    db: Session, principal: Principal, body: BotSettings, *, commit: bool = True
) -> BotSettings:
    db.exec(
        select(Organization)
        .where(Organization.id == principal.org_id)
        .with_for_update()
    ).one()
    row = db.get(BotSetting, principal.org_id) or BotSetting(org_id=principal.org_id)
    row.sqlmodel_update(body.model_dump())
    db.add(row)
    record_audit(
        db,
        org_id=principal.org_id,
        actor_type=principal.actor_type,
        actor_id=str(principal.user_id),
        action="bot_settings.updated",
        target_type="bot_setting",
        target_id=str(principal.org_id),
        details={"retention_days": body.retention_days},
    )
    if commit:
        db.commit()
    else:
        db.flush()
    return body
