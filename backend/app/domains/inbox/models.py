from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
)
from sqlmodel import Field

from app.domains.base import TenantRecord, enum_type, tenant_fk


class InboxKind(StrEnum):
    INITIAL_REVIEW = "initial_review"
    SUBMITTED = "submitted"
    STATUS_CHANGED = "status_changed"
    QUESTION = "question"
    DICTIONARY_CHANGED = "dictionary_changed"
    REVIEW_REQUESTED = "review_requested"
    DECIDED = "decided"
    ADMIN_TODOS = "admin_todos"


class InboxItem(TenantRecord, table=True):
    __tablename__ = "inbox_item"
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint(
            "org_id",
            "recipient_user_id",
            "admin_todo_date",
            name="uq_inbox_admin_todo_day",
        ),
        tenant_fk("recipient_employee_id", "employee"),
        ForeignKeyConstraint(
            ["org_id", "recipient_user_id"],
            ["org_membership.org_id", "org_membership.user_id"],
        ),
        tenant_fk("request_id", "profile_change_request"),
        tenant_fk("question_id", "pending_question"),
        Index("ix_inbox_recipient", "org_id", "recipient_employee_id", "created_at"),
        Index("ix_inbox_user", "org_id", "recipient_user_id", "created_at"),
        CheckConstraint(
            "num_nonnulls(recipient_employee_id, recipient_user_id) = 1",
            name="inbox_one_recipient",
        ),
    )
    recipient_employee_id: UUID | None = None
    recipient_user_id: UUID | None = None
    kind: InboxKind = Field(sa_type=enum_type(InboxKind))
    request_id: UUID | None = None
    question_id: UUID | None = None
    message: str
    admin_todo_date: date | None = Field(default=None, sa_type=Date)
    admin_todo_count: int | None = None
    read_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    done_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class QuestionStatus(StrEnum):
    OPEN = "open"
    ANSWERED = "answered"
    DISMISSED = "dismissed"


class QuestionKind(StrEnum):
    CONFLICT = "conflict"
    STALE = "stale"
    SINGLE_SOURCE = "single_source"
    RECORD = "record"
    GENERAL = "general"


class PendingQuestion(TenantRecord, table=True):
    __tablename__ = "pending_question"
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        tenant_fk("target_employee_id", "employee"),
        tenant_fk("capability_definition_id", "capability_definition"),
        tenant_fk("answer_evidence_id", "evidence"),
        tenant_fk("claim_id", "profile_claim"),
        Index("ix_question_target", "org_id", "target_employee_id", "created_at"),
    )
    target_employee_id: UUID
    kind: QuestionKind = Field(
        default=QuestionKind.GENERAL,
        sa_type=enum_type(QuestionKind),
        sa_column_kwargs={"server_default": "general"},
    )
    claim_id: UUID | None = None
    capability_definition_id: UUID | None = None
    question: str
    reason: str
    status: QuestionStatus = Field(
        default=QuestionStatus.OPEN, sa_type=enum_type(QuestionStatus)
    )
    answer_evidence_id: UUID | None = None
    answered_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
