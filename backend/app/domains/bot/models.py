from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlmodel import Field, SQLModel

from app.domains.base import TenantRecord, tenant_fk, utc_now


class BotSetting(SQLModel, table=True):
    __tablename__ = "bot_setting"
    __table_args__ = (
        CheckConstraint("retention_days BETWEEN 0 AND 30"),
        CheckConstraint("length(question_guide) <= 4000"),
    )
    org_id: UUID = Field(primary_key=True, foreign_key="organization.id")
    question_guide: str = ""
    retention_days: int = 0


class BotSession(SQLModel, table=True):
    __tablename__ = "bot_session"
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        tenant_fk("employee_id", "employee"),
        tenant_fk("pending_question_id", "pending_question"),
        CheckConstraint(
            "channel IN ('web', 'telegram', 'feishu', 'wecom')",
            name="ck_bot_session_channel",
        ),
        CheckConstraint("turn_count >= 0"),
        Index("ix_bot_session_employee", "org_id", "employee_id"),
        Index("ix_bot_session_lease", "org_id", "lease_until"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    org_id: UUID = Field(foreign_key="organization.id", index=True)
    employee_id: UUID
    channel: Literal["web", "telegram", "feishu", "wecom"] = Field(
        default="web", sa_type=String
    )
    external_chat_id: str | None = None
    bot_identity: str | None = None
    pending_question_id: UUID | None = None
    started_at: datetime = Field(
        default_factory=utc_now, sa_type=DateTime(timezone=True)
    )
    last_activity_at: datetime = Field(
        default_factory=utc_now, sa_type=DateTime(timezone=True)
    )
    state_ciphertext: str | None = Field(default=None, repr=False)
    turn_count: int = 0
    request_ids: list[UUID] = Field(default_factory=list, sa_type=ARRAY(Uuid))
    lease_until: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class ConversationMessage(TenantRecord):
    session_id: UUID
    role: Literal["user", "assistant"] = Field(sa_type=String)
    ciphertext: str = Field(repr=False)


class BotMessage(ConversationMessage, table=True):
    __tablename__ = "bot_message"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "session_id"],
            ["bot_session.org_id", "bot_session.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("role IN ('user', 'assistant')"),
        Index("ix_bot_message_session_time", "org_id", "session_id", "created_at"),
    )


class ChatAttachment(TenantRecord):
    session_id: UUID
    kind: Literal["image", "file"] = Field(sa_type=String)
    filename: str
    mime_type: str
    size: int
    ciphertext: str = Field(repr=False)
    text_ciphertext: str | None = Field(default=None, repr=False)
    notice: str | None = None


class BotAttachment(ChatAttachment, table=True):
    __tablename__ = "bot_attachment"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "session_id"],
            ["bot_session.org_id", "bot_session.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("kind IN ('image', 'file')"),
        CheckConstraint("size > 0"),
        Index("ix_bot_attachment_session_time", "org_id", "session_id", "created_at"),
    )
