from typing import Literal

from sqlmodel import SQLModel

from app.domains.setup.models import SetupStatus


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
