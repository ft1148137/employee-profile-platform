from datetime import datetime
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.domains.base import TenantRecord, tenant_fk, utc_now
from app.domains.im.schemas import Platform


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
    platform: Platform = Field(primary_key=True, sa_type=String)
    enabled: bool = False
    credentials: str | None = Field(default=None, repr=False)
    bot_identity: str | None = None
    email_auto_bind: bool = True
    updated_by_user_id: UUID
    updated_at: datetime = Field(
        default_factory=utc_now, sa_type=DateTime(timezone=True)
    )


class ImBinding(TenantRecord, table=True):
    __tablename__ = "im_binding"
    __table_args__ = (
        tenant_fk("employee_id", "employee"),
        UniqueConstraint("org_id", "platform", "external_user_id"),
        UniqueConstraint("org_id", "platform", "employee_id"),
        CheckConstraint("platform IN ('telegram', 'feishu', 'wecom')"),
        CheckConstraint("bound_via IN ('code', 'email')"),
    )
    employee_id: UUID
    platform: Platform = Field(sa_type=String)
    external_user_id: str
    external_chat_id: str
    bot_identity: str
    bound_via: str = "code"
    notify: bool = True


class ImBindCode(TenantRecord, table=True):
    __tablename__ = "im_bind_code"
    __table_args__ = (
        tenant_fk("employee_id", "employee"),
        UniqueConstraint("org_id", "code_hash"),
    )
    employee_id: UUID
    code_hash: str = Field(repr=False)
    expires_at: datetime = Field(sa_type=DateTime(timezone=True))
    used_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class ImEvent(SQLModel, table=True):
    __tablename__ = "im_event"
    org_id: UUID = Field(primary_key=True, foreign_key="organization.id")
    platform: str = Field(primary_key=True)
    event_id: str = Field(primary_key=True)
    received_at: datetime = Field(
        default_factory=utc_now, sa_type=DateTime(timezone=True)
    )


class ImNotification(TenantRecord, table=True):
    __tablename__ = "im_notification"
    __table_args__ = (
        tenant_fk("inbox_item_id", "inbox_item"),
        tenant_fk("employee_id", "employee"),
        UniqueConstraint("org_id", "inbox_item_id", "platform"),
        CheckConstraint("platform IN ('telegram', 'feishu', 'wecom')"),
        CheckConstraint("status IN ('pending', 'sent', 'failed')"),
        CheckConstraint("attempts BETWEEN 0 AND 3"),
        Index("ix_im_notification_pending", "org_id", "status", "created_at"),
    )
    inbox_item_id: UUID
    employee_id: UUID
    platform: Platform = Field(sa_type=String)
    status: str = "pending"
    attempts: int = 0
    last_error_code: str | None = None
    sent_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class ImAction(SQLModel, table=True):
    __tablename__ = "im_action"
    __table_args__ = (
        tenant_fk("employee_id", "employee"),
        tenant_fk("session_id", "bot_session"),
        CheckConstraint("platform IN ('telegram', 'feishu', 'wecom')"),
    )
    token: str = Field(primary_key=True, max_length=22)
    org_id: UUID = Field(foreign_key="organization.id", index=True)
    platform: Platform = Field(sa_type=String)
    bot_identity: str
    employee_id: UUID
    session_id: UUID
    external_chat_id: str
    message_id: str
    action: dict[str, JsonValue] = Field(sa_type=JSONB)
    expires_at: datetime = Field(sa_type=DateTime(timezone=True))
    consumed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
