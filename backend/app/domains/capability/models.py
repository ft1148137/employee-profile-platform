import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import JsonValue
from sqlalchemy import Column, DateTime, Index, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from app.domains.base import TenantRecord, enum_type, tenant_fk


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
