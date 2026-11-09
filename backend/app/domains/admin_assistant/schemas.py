from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.domains.bot.schemas import TextMessage
from app.domains.change_request.schemas import ToolInput

AdminPage = Literal[
    "/admin",
    "/admin/people",
    "/admin/people/$employeeId",
    "/admin/people?tab=accounts",
    "/admin/people?tab=roster",
    "/admin/people?tab=roles",
    "/admin/people?tab=identities",
    "/admin/people?tab=appeals",
    "/admin/rules",
    "/admin/rules?tab=dictionary",
    "/admin/rules?tab=approval",
    "/admin/rules?tab=drafting",
    "/admin/rules?tab=reminders",
    "/admin/rules?tab=profile-import",
    "/admin/data-ai",
    "/admin/data-ai?tab=sources",
    "/admin/data-ai?tab=model",
    "/admin/data-ai?tab=bot",
    "/admin/connections",
    "/admin/connections?tab=im",
    "/admin/connections?tab=mcp",
    "/admin/audit",
    "/setup-wizard",
]


class LinkCard(BaseModel):
    type: Literal["link"] = "link"
    title: str
    page: AdminPage


class AdminTurnInput(ToolInput):
    text: str = Field(min_length=1, max_length=10000, pattern=r"\S")
    page: AdminPage


class AdminTurnResponse(BaseModel):
    messages: list[Annotated[TextMessage | LinkCard, Field(discriminator="type")]]


class AdminSessionMessage(BaseModel):
    id: UUID
    role: Literal["user", "assistant"]
    payload: AdminTurnInput | AdminTurnResponse
    created_at: datetime


class Availability(BaseModel):
    available: bool
    reason: str
