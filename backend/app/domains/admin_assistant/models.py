from datetime import datetime
from uuid import UUID, uuid4

from pydantic import JsonValue
from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.domains.base import TenantRecord, tenant_fk, utc_now
from app.domains.bot.models import ChatAttachment, ConversationMessage


class AdminAssistantSession(SQLModel, table=True):
    __tablename__ = "admin_assistant_session"
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        ForeignKeyConstraint(
            ["org_id", "user_id"], ["org_membership.org_id", "org_membership.user_id"]
        ),
        CheckConstraint("turn_count >= 0"),
        CheckConstraint(
            "purpose IS NULL OR purpose = 'setup'", name="ck_admin_session_purpose"
        ),
        Index(
            "uq_admin_session_setup",
            "org_id",
            "user_id",
            unique=True,
            postgresql_where=text("purpose = 'setup'"),
        ),
        Index("ix_admin_assistant_session_lease", "org_id", "lease_until"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    org_id: UUID = Field(foreign_key="organization.id", index=True)
    user_id: UUID
    started_at: datetime = Field(
        default_factory=utc_now, sa_type=DateTime(timezone=True)
    )
    last_activity_at: datetime = Field(
        default_factory=utc_now, sa_type=DateTime(timezone=True)
    )
    state_ciphertext: str | None = Field(default=None, repr=False)
    purpose: str | None = None
    turn_count: int = 0
    lease_until: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class AdminAssistantMessage(ConversationMessage, table=True):
    __tablename__ = "admin_assistant_message"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "session_id"],
            ["admin_assistant_session.org_id", "admin_assistant_session.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("role IN ('user', 'assistant')"),
        Index(
            "ix_admin_assistant_message_session_time",
            "org_id",
            "session_id",
            "created_at",
        ),
    )


class AdminChangeSet(TenantRecord, table=True):
    # PostgreSQL defers these foreign keys until commit so drafting cannot lock org/session rows during a turn.
    __tablename__ = "admin_change_set"
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        tenant_fk("session_id", "admin_assistant_session"),
        tenant_fk("undo_of_id", "admin_change_set"),
        ForeignKeyConstraint(
            ["org_id", "created_by_user_id"],
            ["org_membership.org_id", "org_membership.user_id"],
        ),
        CheckConstraint("status IN ('draft','applied','discarded','stale','undone')"),
        CheckConstraint("revision >= 1"),
        CheckConstraint(
            "jsonb_typeof(items) = 'array' AND jsonb_array_length(items) BETWEEN 1 AND 50"
        ),
    )
    session_id: UUID
    created_by_user_id: UUID
    status: str = Field(default="draft", sa_type=String)
    revision: int = 1
    items: list[dict[str, JsonValue]] = Field(sa_column=Column(JSONB, nullable=False))
    confirmed_once_at: datetime | None = Field(
        default=None, sa_type=DateTime(timezone=True)
    )
    secrets: str | None = Field(default=None, sa_type=Text, repr=False)
    applied_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    undo_of_id: UUID | None = None
    updated_at: datetime = Field(
        default_factory=utc_now, sa_type=DateTime(timezone=True)
    )


class AdminAssistantAttachment(ChatAttachment, table=True):
    __tablename__ = "admin_assistant_attachment"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "session_id"],
            ["admin_assistant_session.org_id", "admin_assistant_session.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("kind IN ('image', 'file')"),
        CheckConstraint("size > 0"),
        Index(
            "ix_admin_assistant_attachment_session_time",
            "org_id",
            "session_id",
            "created_at",
        ),
    )
