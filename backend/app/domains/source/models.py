from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from app.domains.base import TenantRecord, utc_now

SyncSchedule = Literal["off", "monthly", "quarterly", "yearly"]


class SourceConnection(TenantRecord, table=True):
    __tablename__ = "source_connection"
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "kind"),
        ForeignKeyConstraint(
            ["org_id", "updated_by_user_id"],
            ["org_membership.org_id", "org_membership.user_id"],
        ),
    )
    kind: str
    config: dict[str, JsonValue] = Field(
        default_factory=dict, sa_column=Column(JSONB, nullable=False)
    )
    secret_ciphertext: str | None = Field(default=None, repr=False)
    sync_schedule: SyncSchedule = Field(default="off", sa_type=String)
    next_sync_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    synced_until: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    updated_at: datetime = Field(
        default_factory=utc_now, sa_type=DateTime(timezone=True)
    )
    updated_by_user_id: UUID


class SyncRun(TenantRecord, table=True):
    __tablename__ = "sync_run"
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        ForeignKeyConstraint(
            ["org_id", "connection_id"],
            ["source_connection.org_id", "source_connection.id"],
        ),
        ForeignKeyConstraint(
            ["org_id", "requested_by_user_id"],
            ["org_membership.org_id", "org_membership.user_id"],
        ),
        Index(
            "uq_sync_run_active",
            "org_id",
            "connection_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )
    connection_id: UUID
    status: str = "queued"
    trigger: str = "manual"
    attempts: int = 0
    cursor: dict[str, JsonValue] = Field(
        default_factory=dict, sa_column=Column(JSONB, nullable=False)
    )
    counts: dict[str, int] = Field(
        default_factory=dict, sa_column=Column(JSONB, nullable=False)
    )
    error: str | None = None
    input: dict[str, JsonValue] | None = Field(
        default=None, repr=False, sa_column=Column(JSONB)
    )
    requested_by_user_id: UUID
    started_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    finished_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    updated_at: datetime = Field(
        default_factory=utc_now, sa_type=DateTime(timezone=True)
    )
