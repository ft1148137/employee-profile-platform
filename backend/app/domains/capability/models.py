import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import JsonValue
from sqlalchemy import (
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


class SchemaStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    RETIRED = "retired"


class CapabilitySchemaVersion(TenantRecord, table=True):
    __tablename__ = "capability_schema_version"
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "version"),
        Index(
            "uq_active_schema_per_org",
            "org_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )
    version: int
    status: SchemaStatus = Field(
        default=SchemaStatus.DRAFT, sa_type=enum_type(SchemaStatus)
    )
    template_ref: str | None = None
    notes: str | None = None
    activated_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class CapabilityDefinition(TenantRecord, table=True):
    __tablename__ = "capability_definition"
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("schema_version_id", "key"),
        tenant_fk("schema_version_id", "capability_schema_version"),
    )
    schema_version_id: uuid.UUID
    key: str
    position: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
    name: str
    category: str | None = None
    description: str | None = None
    level_rubric: dict[str, JsonValue] | None = Field(
        default=None, sa_column=Column(JSONB, nullable=True)
    )


class ProposalStatus(StrEnum):
    OPEN = "open"
    ACCEPTED = "accepted"
    DISMISSED = "dismissed"


class CapabilityProposal(TenantRecord, table=True):
    __tablename__ = "capability_proposal"
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        ForeignKeyConstraint(
            ["org_id", "decided_by_user_id"],
            ["org_membership.org_id", "org_membership.user_id"],
        ),
        Index(
            "uq_open_proposal_name",
            "org_id",
            "name_key",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
    )
    name: str = Field(max_length=40)
    name_key: str
    category: str | None = Field(default=None, max_length=40)
    description: str = Field(max_length=300)
    reason: str = Field(max_length=300)
    status: ProposalStatus = Field(
        default=ProposalStatus.OPEN, sa_type=enum_type(ProposalStatus)
    )
    updated_at: datetime = Field(
        default_factory=utc_now, sa_type=DateTime(timezone=True)
    )
    decided_by_user_id: uuid.UUID | None = None
    decided_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class CapabilityProposalEvidence(SQLModel, table=True):
    __tablename__ = "capability_proposal_evidence"
    __table_args__ = (
        tenant_fk("proposal_id", "capability_proposal"),
        tenant_fk("evidence_id", "evidence"),
    )
    org_id: uuid.UUID = Field(primary_key=True)
    proposal_id: uuid.UUID = Field(primary_key=True)
    evidence_id: uuid.UUID = Field(primary_key=True)
