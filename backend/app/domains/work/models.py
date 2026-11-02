import uuid
from datetime import datetime

from pydantic import JsonValue
from sqlalchemy import Column, DateTime, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from app.domains.base import TenantRecord, tenant_fk


class Task(TenantRecord, table=True):
    __tablename__ = "task"
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "source", "external_key"),
    )
    source: str
    external_key: str
    title: str
    task_type: str | None = None
    status: str | None = None
    url: str | None = None
    attributes: dict[str, JsonValue] = Field(
        default_factory=dict, sa_column=Column(JSONB, nullable=False)
    )
    source_created_at: datetime | None = Field(
        default=None, sa_type=DateTime(timezone=True)
    )
    source_updated_at: datetime | None = Field(
        default=None, sa_type=DateTime(timezone=True)
    )


class WorkEvent(TenantRecord, table=True):
    __tablename__ = "work_event"
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "source", "source_ref"),
        tenant_fk("actor_employee_id", "employee"),
        tenant_fk("task_id", "task"),
    )
    actor_employee_id: uuid.UUID
    task_id: uuid.UUID | None = None
    action: str
    occurred_at: datetime = Field(sa_type=DateTime(timezone=True))
    outcome: str | None = None
    source: str
    source_ref: str
