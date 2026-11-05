from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator
from sqlmodel import SQLModel

from app.domains.change_request.schemas import ToolInput
from app.domains.review.models import AppealStatus


class AppealReason(ToolInput):
    reason: str = Field(min_length=1, max_length=4000)

    @field_validator("reason")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("必须填写原因或说明")
        return value.strip()


class AppealInput(AppealReason):
    request_id: UUID
    item_id: UUID


class ResolveAppealInput(AppealReason):
    status: Literal["upheld", "reopened"]


class AppealPublic(SQLModel):
    id: UUID
    request_id: UUID
    item_id: UUID
    employee_id: UUID
    reason: str
    status: AppealStatus
    created_at: datetime
    resolved_at: datetime | None
    resolution_note: str | None


class AppealResolution(SQLModel):
    appeal: AppealPublic
    draft_request_id: UUID | None = None
