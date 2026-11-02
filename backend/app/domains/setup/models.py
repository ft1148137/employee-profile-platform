from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime
from sqlmodel import Field

from app.domains.base import Record, enum_type


class SetupPurpose(StrEnum):
    INITIAL_SETUP = "initial_setup"
    NEW_ORG = "new_org"


class SetupToken(Record, table=True):
    __tablename__ = "setup_token"
    token_hash: str = Field(max_length=64, unique=True)
    purpose: SetupPurpose = Field(sa_type=enum_type(SetupPurpose))
    expires_at: datetime = Field(sa_type=DateTime(timezone=True))
    used_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
