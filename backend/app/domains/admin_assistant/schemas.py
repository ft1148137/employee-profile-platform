from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, JsonValue, SecretStr

from app.domains.admin_assistant.outputs import OneTimeOutput
from app.domains.admin_assistant.registry import TableCard as TableCard
from app.domains.bot.schemas import TextMessage
from app.domains.change_request.schemas import ToolInput
from app.domains.org.roster.schemas import RosterImportPreview

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
    page: str


class AdminTurnInput(ToolInput):
    text: str = Field(min_length=1, max_length=10000, pattern=r"\S")
    page: AdminPage


class ChangeSpec(ToolInput):
    function: str
    args: dict[str, JsonValue]


class ProposeChanges(ToolInput):
    items: list[ChangeSpec] = Field(min_length=1, max_length=50)
    change_set_id: UUID | None = None


class Impact(BaseModel):
    count: int = Field(ge=0)
    text: str


class ChangeItem(ChangeSpec):
    title: str = ""
    undoable: bool = True
    before: dict[str, JsonValue] | None
    after: dict[str, JsonValue]
    impact: Impact | None = None
    risk: Literal["normal", "high"]


class ChangeSetPublic(BaseModel):
    id: UUID
    revision: int
    status: Literal["draft", "applied", "discarded", "stale", "undone"]
    items: list[ChangeItem]
    undo_of_id: UUID | None
    confirmed_once_at: datetime | None = None


class ChangeSetApplied(ChangeSetPublic):
    one_time: list[OneTimeOutput] = Field(default_factory=list)


class SecretInputCard(BaseModel):
    type: Literal["secret_input"] = "secret_input"
    change_set_id: UUID
    field: str
    title: str


class SecretInput(ToolInput):
    value: SecretStr = Field(min_length=1, max_length=10000)


class ChangeSetCard(BaseModel):
    type: Literal["admin_change_set"] = "admin_change_set"
    change_set: ChangeSetPublic


class RequestFile(ToolInput):
    kind: Literal["roster"]


class FileUploadCard(RequestFile):
    type: Literal["file_upload"] = "file_upload"


class FilePreviewCard(RequestFile):
    type: Literal["file_preview"] = "file_preview"
    preview: RosterImportPreview


class ApplyChanges(ToolInput):
    revision: int = Field(ge=1, strict=True)


class AdminTurnResponse(BaseModel):
    messages: list[
        Annotated[
            TextMessage
            | LinkCard
            | TableCard
            | ChangeSetCard
            | SecretInputCard
            | FileUploadCard
            | FilePreviewCard,
            Field(discriminator="type"),
        ]
    ]


class AdminSessionMessage(BaseModel):
    id: UUID
    role: Literal["user", "assistant"]
    payload: AdminTurnInput | AdminTurnResponse
    created_at: datetime


class Availability(BaseModel):
    available: bool
    reason: str
