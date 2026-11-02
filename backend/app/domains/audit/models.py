import uuid
from enum import StrEnum

from pydantic import JsonValue
from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from app.domains.base import Record, enum_type


class ActorKind(StrEnum):
    USER = "user"
    SYSTEM_WORKER = "system_worker"
    AGENT = "agent"
    SEED = "seed"


class AuditEvent(Record, table=True):
    """Append-only audit trail; org_id=None denotes a platform event."""

    __tablename__ = "audit_event"
    org_id: uuid.UUID | None = Field(
        default=None, foreign_key="organization.id", index=True
    )
    actor_type: ActorKind = Field(sa_type=enum_type(ActorKind))
    actor_id: str | None = None
    action: str
    target_type: str
    target_id: str | None = None
    details: dict[str, JsonValue] = Field(sa_column=Column(JSONB, nullable=False))
