import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Index
from sqlmodel import Field, SQLModel


class LoginAttempt(SQLModel, table=True):
    __tablename__ = "login_attempt"
    __table_args__ = (
        Index("ix_login_attempt_identifier_created", "identifier_norm", "created_at"),
        Index("ix_login_attempt_ip_created", "ip", "created_at"),
    )
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    identifier_norm: str = Field(max_length=512)
    ip: str = Field(max_length=255)
    succeeded: bool
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), sa_type=DateTime(timezone=True)
    )
