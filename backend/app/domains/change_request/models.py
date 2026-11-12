import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import JsonValue
from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.domains.base import TenantRecord, enum_type, tenant_fk, utc_now
from app.domains.profile.models import EvidenceRelation


class RequestOrigin(StrEnum):
    INITIAL_SYNC = "initial_sync"
    INCREMENTAL_SYNC = "incremental_sync"
    CONVERSATION = "conversation"
    MANUAL = "manual"
    CORRECTION = "correction"
    IMPORT = "import"


class SubmissionVia(StrEnum):
    WEB = "web"
    MCP = "mcp"
    IMPORT = "import"
    BOT = "bot"
    ADMIN_HANDOVER = "admin_handover"


class RequestStatus(StrEnum):
    DRAFT = "draft"
    PENDING_OWNER = "pending_owner"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class ChangeOp(StrEnum):
    ADD = "add"
    MODIFY = "modify"
    RETRACT = "retract"
    ATTACH_EVIDENCE = "attach_evidence"


class ItemDecision(StrEnum):
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    REJECTED = "rejected"


class ReviewVia(StrEnum):
    DIRECT = "direct"
    WEB = "web"
    MCP = "mcp"
    IM = "im"
    POLICY = "policy"
    SYNC = "sync"


class ReviewDecision(TenantRecord, table=True):
    __tablename__ = "review_decision"
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        tenant_fk("request_id", "profile_change_request"),
        ForeignKeyConstraint(
            ["org_id", "reviewer_user_id"],
            ["org_membership.org_id", "org_membership.user_id"],
        ),
        ForeignKeyConstraint(
            ["org_id", "acting_for_user_id"],
            ["org_membership.org_id", "org_membership.user_id"],
        ),
        CheckConstraint(
            "(via IN ('policy', 'sync')) = (reviewer_user_id IS NULL)",
            name="review_decision_actor",
        ),
    )
    request_id: uuid.UUID
    reviewer_user_id: uuid.UUID | None = None
    acting_for_user_id: uuid.UUID | None = None
    via: ReviewVia = Field(sa_type=enum_type(ReviewVia))
    client_name: str | None = None
    comment: str | None = None
    request_revision: int


class ProfileChangeRequest(TenantRecord, table=True):
    __tablename__ = "profile_change_request"
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        tenant_fk("subject_employee_id", "employee"),
        tenant_fk("base_version_id", "profile_version"),
        tenant_fk("parent_request_id", "profile_change_request"),
        Index("ix_request_employee_status", "org_id", "subject_employee_id", "status"),
        Index(
            "uq_request_initial_sync",
            "org_id",
            "subject_employee_id",
            unique=True,
            postgresql_where=text("origin = 'initial_sync'"),
        ),
    )
    subject_employee_id: uuid.UUID
    origin: RequestOrigin = Field(sa_type=enum_type(RequestOrigin))
    status: RequestStatus = Field(sa_type=enum_type(RequestStatus))
    author: dict[str, JsonValue] = Field(sa_column=Column(JSONB, nullable=False))
    base_version_id: uuid.UUID | None = None
    parent_request_id: uuid.UUID | None = None
    decided_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    generated_by: dict[str, JsonValue] = Field(
        default_factory=dict, sa_column=Column(JSONB, nullable=False)
    )
    revision: int = Field(default=1, sa_column_kwargs={"server_default": "1"})
    submitted_via: SubmissionVia | None = Field(
        default=None, sa_type=enum_type(SubmissionVia)
    )
    client_name: str | None = None
    submitted_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    updated_at: datetime = Field(
        default_factory=utc_now, sa_type=DateTime(timezone=True)
    )


class ChangeItem(TenantRecord, table=True):
    __tablename__ = "change_item"
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("request_id", "position"),
        tenant_fk("request_id", "profile_change_request"),
        tenant_fk("capability_definition_id", "capability_definition"),
        tenant_fk("target_claim_id", "profile_claim"),
        tenant_fk("review_decision_id", "review_decision"),
        tenant_fk("published_claim_id", "profile_claim"),
        CheckConstraint(
            "(op = 'add') = (target_claim_id IS NULL)", name="change_item_target"
        ),
        CheckConstraint(
            "(op = 'retract' AND after IS NULL) OR (op IN ('add', 'modify') AND after IS NOT NULL) OR op = 'attach_evidence'",
            name="change_item_after",
        ),
    )
    request_id: uuid.UUID
    position: int
    op: ChangeOp = Field(sa_type=enum_type(ChangeOp))
    capability_definition_id: uuid.UUID
    target_claim_id: uuid.UUID | None = None
    after: dict[str, JsonValue] | None = Field(
        default=None, sa_column=Column(JSONB(none_as_null=True))
    )
    explanation: str | None = None
    uncertainty: str | None = None
    owner_statement: str | None = None
    objection: str | None = None
    decision: ItemDecision | None = Field(default=None, sa_type=enum_type(ItemDecision))
    decision_reason: str | None = None
    review_decision_id: uuid.UUID | None = None
    published_claim_id: uuid.UUID | None = None


class ChangeItemEvidence(SQLModel, table=True):
    __tablename__ = "change_item_evidence"
    __table_args__ = (
        tenant_fk("item_id", "change_item"),
        tenant_fk("evidence_id", "evidence"),
    )
    org_id: uuid.UUID = Field(foreign_key="organization.id", index=True)
    item_id: uuid.UUID = Field(primary_key=True)
    evidence_id: uuid.UUID = Field(primary_key=True)
    relation: EvidenceRelation = Field(sa_type=enum_type(EvidenceRelation))
