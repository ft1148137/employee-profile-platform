from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import DateTime, Index, UniqueConstraint
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


class InboxItem(TenantRecord, table=True):
    __tablename__ = "inbox_item"
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        tenant_fk("recipient_employee_id", "employee"),
        tenant_fk("request_id", "profile_change_request"),
        tenant_fk("question_id", "pending_question"),
        Index("ix_inbox_recipient", "org_id", "recipient_employee_id", "created_at"),
    )
    recipient_employee_id: UUID
    kind: InboxKind = Field(sa_type=enum_type(InboxKind))
    request_id: UUID | None = None
    question_id: UUID | None = None
    message: str
    read_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    done_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class QuestionStatus(StrEnum):
    OPEN = "open"
    ANSWERED = "answered"
    DISMISSED = "dismissed"


class PendingQuestion(TenantRecord, table=True):
    __tablename__ = "pending_question"
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        tenant_fk("target_employee_id", "employee"),
        tenant_fk("capability_definition_id", "capability_definition"),
        tenant_fk("answer_evidence_id", "evidence"),
        Index("ix_question_target", "org_id", "target_employee_id", "created_at"),
    )
    target_employee_id: UUID
    capability_definition_id: UUID | None = None
    question: str
    reason: str
    status: QuestionStatus = Field(
        default=QuestionStatus.OPEN, sa_type=enum_type(QuestionStatus)
    )
    answer_evidence_id: UUID | None = None
    answered_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
