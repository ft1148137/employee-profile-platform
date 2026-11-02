import uuid
from datetime import UTC, datetime

from pydantic import EmailStr, field_validator
from sqlalchemy import DateTime, Index, text
from sqlmodel import Field, SQLModel

from app import domains as domains


def get_datetime_utc() -> datetime:
    return datetime.now(UTC)


# Shared properties
class UserBase(SQLModel):
    username: str = Field(min_length=1, max_length=255)
    email: EmailStr | None = Field(default=None, max_length=255)
    is_active: bool = True
    is_superuser: bool = False
    full_name: str | None = Field(default=None, max_length=255)

    @field_validator("username", mode="before")
    @classmethod
    def trim_username(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


# Properties to receive via API on creation
class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=128)


# Properties to receive via API on update, all are optional
class UserUpdate(SQLModel):
    email: EmailStr | None = Field(default=None, max_length=255)
    is_active: bool | None = None
    is_superuser: bool | None = None
    full_name: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, min_length=8, max_length=128)


class UserUpdateMe(SQLModel):
    full_name: str | None = Field(default=None, max_length=255)
    email: EmailStr | None = Field(default=None, max_length=255)


class UpdatePassword(SQLModel):
    current_password: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


# Database model, database table inferred from class name
class User(UserBase, table=True):
    __table_args__ = (
        Index("ix_user_username_lower", text("lower(username)"), unique=True),
        Index(
            "ix_user_email_lower",
            text("lower(email)"),
            unique=True,
            postgresql_where=text("email IS NOT NULL"),
        ),
    )
    must_change_password: bool = False
    password_changed_at: datetime | None = Field(
        default=None, sa_type=DateTime(timezone=True)
    )
    last_login_at: datetime | None = Field(
        default=None, sa_type=DateTime(timezone=True)
    )
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    hashed_password: str
    created_at: datetime | None = Field(default_factory=get_datetime_utc)


# Properties to return via API, id is always required
class UserPublic(UserBase):
    must_change_password: bool = False
    last_login_at: datetime | None = None
    id: uuid.UUID
    created_at: datetime | None = None


class UsersPublic(SQLModel):
    data: list[UserPublic]
    count: int


# Generic message
class Message(SQLModel):
    message: str


# JSON payload containing access token
class Token(SQLModel):
    access_token: str
    token_type: str = "bearer"


# Contents of JWT token
class TokenPayload(SQLModel):
    sub: uuid.UUID
    iat: int
    password_version: str | None = None
