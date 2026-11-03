from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKeyConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.domains.base import utc_now


class ModelSetting(SQLModel, table=True):
    __tablename__ = "model_setting"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "updated_by_user_id"],
            ["org_membership.org_id", "org_membership.user_id"],
        ),
    )
    org_id: UUID = Field(primary_key=True, foreign_key="organization.id")
    enabled: bool = False
    kind: str | None = Field(default=None, max_length=40)
    model: str | None = Field(default=None, max_length=200)
    base_url: str | None = Field(default=None, max_length=500)
    api_key_ciphertext: str | None = Field(default=None, repr=False)
    reasoning_effort: str | None = Field(default=None, max_length=10)
    chatgpt_auth_ciphertext: str | None = Field(default=None, repr=False)
    chatgpt_account: dict[str, Any] | None = Field(default=None, sa_type=JSONB)
    chatgpt_login_ciphertext: str | None = Field(default=None, repr=False)
    chatgpt_login_expires_at: datetime | None = Field(
        default=None, sa_type=DateTime(timezone=True)
    )
    chatgpt_login_polled_at: datetime | None = Field(
        default=None, sa_type=DateTime(timezone=True)
    )
    paused_until: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    pause_reason: str | None = None
    updated_by_user_id: UUID
    updated_at: datetime = Field(
        default_factory=utc_now, sa_type=DateTime(timezone=True)
    )
