from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, String
from sqlmodel import Field, SQLModel

from app.domains.base import utc_now


class ImSetting(SQLModel, table=True):
    __tablename__ = "im_setting"
    __table_args__ = (
        CheckConstraint("platform IN ('feishu', 'wecom', 'telegram')"),
        ForeignKeyConstraint(
            ["org_id", "updated_by_user_id"],
            ["org_membership.org_id", "org_membership.user_id"],
        ),
    )
    org_id: UUID = Field(primary_key=True, foreign_key="organization.id")
    platform: str = Field(primary_key=True, sa_type=String)
    enabled: bool = False
    credentials: str | None = Field(default=None, repr=False)
    email_auto_bind: bool = True
    updated_by_user_id: UUID
    updated_at: datetime = Field(
        default_factory=utc_now, sa_type=DateTime(timezone=True)
    )
