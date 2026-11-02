from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import DateTime
from sqlmodel import Field, SQLModel

from app.domains.base import Record, enum_type, utc_now


class SetupPurpose(StrEnum):
    INITIAL_SETUP = "initial_setup"
    NEW_ORG = "new_org"


class SetupToken(Record, table=True):
    __tablename__ = "setup_token"
    token_hash: str = Field(max_length=64, unique=True)
    purpose: SetupPurpose = Field(sa_type=enum_type(SetupPurpose))
    expires_at: datetime = Field(sa_type=DateTime(timezone=True))
    used_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class SetupStatus(StrEnum):
    COMPLETED = "completed"
    SKIPPED = "skipped"


class SetupStepState(SQLModel, table=True):
    __tablename__ = "setup_step_state"

    org_id: UUID = Field(foreign_key="organization.id", primary_key=True)
    step_key: str = Field(primary_key=True)
    status: SetupStatus = Field(sa_type=enum_type(SetupStatus))
    updated_by_user_id: UUID = Field(foreign_key="user.id")
    updated_at: datetime = Field(
        default_factory=utc_now, sa_type=DateTime(timezone=True)
    )
