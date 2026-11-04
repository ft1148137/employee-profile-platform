from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domains.change_request.schemas import ToolInput
from app.domains.profile.models import AssessmentStatus


class Projection(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Capability(Projection):
    key: str
    name: str


class Level(Projection):
    key: str
    label: str
    description: str


class Rubric(Projection):
    levels: list[Level] = Field(default_factory=list)


class DictionaryCapability(Capability):
    description: str | None
    level_rubric: Rubric | None
    proactive: bool = True


class Dictionary(Projection):
    data: list[DictionaryCapability]


class Claim(Projection):
    statement: str
    level: str | None
    assessment_status: AssessmentStatus


class ProfileItem(Projection):
    capability: Capability
    status: str
    claim: Claim | None


class Profile(Projection):
    items: list[ProfileItem]
    auto_draft_enabled: bool = True


class Evidence(Projection):
    id: UUID
    kind: str
    title: str
    status: str
    redacted: bool


class EvidenceList(Projection):
    data: list[Evidence]
    count: int


class ChangeItem(Projection):
    op: str
    capability: Capability
    after: Claim | None
    explanation: str | None
    uncertainty: str | None
    owner_statement: str | None
    objection: str | None


class Request(Projection):
    id: UUID
    revision: int
    status: str
    items: list[ChangeItem]


class Requests(Projection):
    data: list[Request]
    count: int


class PreviewItem(Projection):
    before: Claim | None
    after: ChangeItem
    warning: str | None


class Preview(Projection):
    request_id: UUID
    revision: int
    items: list[PreviewItem]


class Question(Projection):
    id: UUID
    question: str
    reason: str
    status: str


class Questions(Projection):
    data: list[Question]
    count: int


class RequestAction(ToolInput):
    action: Literal["confirm_submit", "continue_editing", "withdraw"]
    request_id: UUID
    revision: int = Field(ge=1)


class QuestionAction(ToolInput):
    action: Literal["answer_question"]
    question_id: UUID
    answer: str = Field(min_length=1, max_length=10000, pattern=r"\S")


Action = RequestAction | QuestionAction


class TextMessage(BaseModel):
    type: Literal["text"] = "text"
    text: str


class PreviewCard(Preview):
    type: Literal["preview_card"] = "preview_card"
    actions: list[RequestAction]


class StatusCard(Request):
    type: Literal["status_card"] = "status_card"
    actions: list[RequestAction]


class QuestionCard(Question):
    type: Literal["question_card"] = "question_card"
    actions: list[Literal["answer_question"]]


Message = Annotated[
    TextMessage | PreviewCard | StatusCard | QuestionCard, Field(discriminator="type")
]


class TurnResponse(BaseModel):
    messages: list[Message]


class TurnInput(ToolInput):
    text: str = Field(min_length=1, max_length=10000, pattern=r"\S")


class SessionCreated(BaseModel):
    session_id: UUID


class SessionMessage(BaseModel):
    id: UUID
    role: Literal["user", "assistant"]
    payload: TurnInput | TurnResponse
    created_at: datetime
