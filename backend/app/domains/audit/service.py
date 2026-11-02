from uuid import UUID

from pydantic import JsonValue
from sqlmodel import Session

from app.domains.audit.models import ActorKind, AuditEvent


def record_audit(
    session: Session,
    *,
    org_id: UUID | None,
    actor_type: ActorKind,
    actor_id: str | None,
    action: str,
    target_type: str,
    target_id: str | None,
    details: dict[str, JsonValue],
) -> AuditEvent:
    """Append an audit event in the caller's transaction without committing."""
    event = AuditEvent(
        org_id=org_id,
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        details=details,
    )
    session.add(event)
    session.flush()
    return event
