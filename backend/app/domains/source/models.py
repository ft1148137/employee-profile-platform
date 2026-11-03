from datetime import datetime
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import Column, DateTime, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from app.domains.base import TenantRecord, utc_now


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
    synced_until: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    updated_at: datetime = Field(
        default_factory=utc_now, sa_type=DateTime(timezone=True)
    )
    updated_by_user_id: UUID
