from uuid import UUID

from sqlmodel import Session, select

from app.authz.principal import Principal
from app.domains.admin_assistant.models import AdminAssistantSession
from app.domains.bot import store as messages


def create_session(db: Session, principal: Principal) -> AdminAssistantSession:
    if not principal.is_org_admin or principal.user_id is None:
        raise messages.BotSessionNotFound()
    row = AdminAssistantSession(org_id=principal.org_id, user_id=principal.user_id)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_session(
    db: Session, principal: Principal, session_id: UUID, *, lock: bool = False
) -> AdminAssistantSession:
    query = (
        select(AdminAssistantSession)
        .where(
            AdminAssistantSession.org_id == principal.org_id,
            AdminAssistantSession.user_id == principal.user_id,
            AdminAssistantSession.id == session_id,
        )
        .execution_options(populate_existing=True)
    )
    row = db.exec(query.with_for_update() if lock else query).one_or_none()
    if row is None or not principal.is_org_admin:
        raise messages.BotSessionNotFound()
    return row
