from datetime import datetime
from uuid import UUID

from pydantic import Field
from sqlmodel import SQLModel

from app.domains.change_request.schemas import ToolInput
from app.domains.inbox.models import InboxKind, QuestionStatus


class InboxPublic(SQLModel):
    id: UUID
    kind: InboxKind
    request_id: UUID | None
    question_id: UUID | None
    message: str
    created_at: datetime
    read_at: datetime | None
    done_at: datetime | None


class InboxList(SQLModel):
    data: list[InboxPublic]
    count: int
    unread_count: int
    pending_count: int


class QuestionsInput(ToolInput):
    skip: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=100)


class AnswerInput(ToolInput):
    answer: str = Field(min_length=1, max_length=10000, pattern=r"\S")


class AnswerQuestionInput(AnswerInput):
    question_id: UUID


class QuestionPublic(SQLModel):
    id: UUID
    capability_definition_id: UUID | None
    question: str
    reason: str
    status: QuestionStatus
    answer_evidence_id: UUID | None
    created_at: datetime
    answered_at: datetime | None


class QuestionsList(SQLModel):
    data: list[QuestionPublic]
    count: int
