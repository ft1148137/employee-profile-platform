from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint
from sqlmodel import Field, SQLModel

from app.domains.base import utc_now


class DraftingNote(SQLModel, table=True):
    __tablename__ = "drafting_note"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "created_by_user_id"],
            ["org_membership.org_id", "org_membership.user_id"],
        ),
        CheckConstraint("version >= 1"),
        CheckConstraint("length(text) <= 2000"),
    )
    org_id: UUID = Field(primary_key=True, foreign_key="organization.id")
    version: int = Field(primary_key=True)
    text: str = Field(max_length=2000)
    created_by_user_id: UUID
    created_at: datetime = Field(
        default_factory=utc_now, sa_type=DateTime(timezone=True)
    )


class DraftingSetting(SQLModel, table=True):
    __tablename__ = "drafting_setting"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "updated_by_user_id"],
            ["org_membership.org_id", "org_membership.user_id"],
        ),
    )
    org_id: UUID = Field(primary_key=True, foreign_key="organization.id")
    auto_draft_enabled: bool = Field(
        default=False, sa_column_kwargs={"server_default": "false"}
    )
    updated_by_user_id: UUID
    updated_at: datetime = Field(
        default_factory=utc_now, sa_type=DateTime(timezone=True)
    )


class UpdateSetting(SQLModel, table=True):
    __tablename__ = "update_setting"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "updated_by_user_id"],
            ["org_membership.org_id", "org_membership.user_id"],
        ),
        CheckConstraint(
            "reminder_schedule IN ('quarterly', 'yearly', 'off')",
            name="ck_update_schedule",
        ),
        CheckConstraint(
            "stale_after_months BETWEEN 6 AND 120", name="ck_update_stale_months"
        ),
    )
    org_id: UUID = Field(primary_key=True, foreign_key="organization.id")
    reminder_schedule: str = "quarterly"
    stale_after_months: int = 24
    next_reminder_at: datetime | None = Field(
        default=None, sa_type=DateTime(timezone=True)
    )
    updated_by_user_id: UUID | None = None
    updated_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
