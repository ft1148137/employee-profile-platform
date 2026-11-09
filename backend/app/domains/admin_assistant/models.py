from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
)
from sqlmodel import Field, SQLModel

from app.domains.base import utc_now
from app.domains.bot.models import ConversationMessage


class AdminAssistantSession(SQLModel, table=True):
    __tablename__ = "admin_assistant_session"
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        ForeignKeyConstraint(
            ["org_id", "user_id"], ["org_membership.org_id", "org_membership.user_id"]
        ),
        CheckConstraint("turn_count >= 0"),
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
