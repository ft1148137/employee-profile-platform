from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, StringConstraints
from sqlmodel import Field, SQLModel

from app.domains.setup.models import SetupPurpose, SetupStatus
from app.models import Token


class InitializationStatus(BaseModel):
    initialized: bool


class InitializeInput(SQLModel):
    token: str = Field(min_length=1, max_length=256)
    org_name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ]
    admin_username: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
    ]
    admin_password: str = Field(min_length=8, max_length=128)
    admin_full_name: str | None = Field(default=None, max_length=255)


class InitializeResult(Token):
    org_id: UUID


class SetupTokenInput(BaseModel):
    purpose: SetupPurpose


class SetupTokenResult(BaseModel):
    token: str


class SetupStepPublic(SQLModel):
    key: str
    title: str
    description: str
    order: int
    required: bool
    skippable: bool
    status: SetupStatus | Literal["pending"]
    blockers: list[str]


class SetupPublic(SQLModel):
    steps: list[SetupStepPublic]
    completed: bool
    next_step_key: str | None
