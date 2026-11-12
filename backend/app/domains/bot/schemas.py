from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domains.bot.attachments import AttachmentPublic
from app.domains.change_request.schemas import ToolInput
from app.domains.profile.models import AssessmentStatus
from app.domains.review.direct import DirectEditInput, DirectEditResult
from app.domains.review.schemas import DecisionInput


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


class ReviewAction(ToolInput):
    action: Literal["confirm_review", "continue_review"]
    request_id: UUID
    revision: int = Field(ge=1)
    card_id: UUID


class ProfileEditAction(ToolInput):
    action: Literal["confirm_profile_edit", "continue_profile_edit"]
    card_id: UUID


Action = RequestAction | QuestionAction | ReviewAction | ProfileEditAction


class TextMessage(BaseModel):
    type: Literal["text"] = "text"
    text: str
    review_link: Literal["/reviews"] | None = None


class ReviewAfter(Projection):
    id: UUID
    op: str
    capability: Capability
    after: Claim | None


class EvidenceTitle(Projection):
    title: str


class ReviewItem(Projection):
    before: Claim | None
    after: ReviewAfter
    evidence: list[EvidenceTitle]
    jira_only: bool
    approvable: bool
    approval_blocked_reason: str | None


class Review(Projection):
    id: UUID
    revision: int
    items: list[ReviewItem]


class ReviewCard(Review):
    type: Literal["review_card"] = "review_card"
    card_id: UUID = Field(default_factory=uuid4)
    decisions: list[DecisionInput]
    actions: list[ReviewAction] = Field(default_factory=list)


class ProfileEditCard(DirectEditResult):
    type: Literal["profile_edit_card"] = "profile_edit_card"
    card_id: UUID = Field(default_factory=uuid4)
    employee_id: UUID
    employee_name: str
    edit: DirectEditInput
    actions: list[ProfileEditAction] = Field(default_factory=list)


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
    TextMessage
    | PreviewCard
    | StatusCard
    | QuestionCard
    | ReviewCard
    | ProfileEditCard,
    Field(discriminator="type"),
]


class ToolStep(BaseModel):
    name: str
    ok: bool


class TurnResponse(BaseModel):
    messages: list[Message]
    steps: list[ToolStep] = Field(default_factory=list)


class TurnInput(ToolInput):
    text: str = Field(min_length=1, max_length=10000, pattern=r"\S")
    attachment_ids: list[UUID] = Field(default_factory=list, max_length=5)

    @field_validator("attachment_ids", mode="before")
    @classmethod
    def limit_attachments(cls, value: object) -> object:
        if isinstance(value, list) and len(value) > 5:
            raise ValueError("每轮最多 5 个附件")
        return value


class UserMessage(TurnInput):
    attachments: list[AttachmentPublic] = Field(default_factory=list)


class SessionCreated(BaseModel):
    session_id: UUID


class SessionMessage(BaseModel):
    id: UUID
    role: Literal["user", "assistant"]
    payload: UserMessage | TurnResponse
    created_at: datetime
