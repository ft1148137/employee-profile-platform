from datetime import datetime
from uuid import UUID

from sqlmodel import SQLModel

from app.domains.inbox.models import InboxKind


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
