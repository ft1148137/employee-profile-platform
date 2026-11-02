from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, JsonValue

from app.domains.audit.models import ActorKind


class AuditActor(BaseModel):
    user_id: UUID
    username: str
    full_name: str | None


class AuditEventPublic(BaseModel):
    id: UUID
    created_at: datetime
    actor_type: ActorKind
    actor: AuditActor | None
    action: str
    target_type: str
    target_id: str | None
    target_label: str | None
    details: dict[str, JsonValue]


class AuditEventsPublic(BaseModel):
    data: list[AuditEventPublic]
    count: int
