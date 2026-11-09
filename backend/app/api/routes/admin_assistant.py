from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.deps import SessionDep
from app.authz.deps import OrgAdminDep, OrgPrincipalDep
from app.domains.admin_assistant import core, store
from app.domains.admin_assistant.schemas import (
    AdminSessionMessage,
    AdminTurnInput,
    AdminTurnResponse,
    Availability,
)
from app.domains.bot.schemas import SessionCreated
from app.domains.bot.store import BotSessionNotFound, messages_for


def require_admin(principal: OrgPrincipalDep) -> None:
    # Conceal private assistant sessions from non-administrators as well.
    if not principal.is_org_admin:
        raise BotSessionNotFound()


router = APIRouter(
    prefix="/admin-assistant",
    tags=["admin-assistant"],
    dependencies=[Depends(require_admin)],
)


@router.post("/sessions", status_code=201)
def create_session(principal: OrgAdminDep, session: SessionDep) -> SessionCreated:
    return SessionCreated(session_id=store.create_session(session, principal).id)


@router.get("/sessions/{session_id}/messages")
def list_messages(
    principal: OrgAdminDep, session: SessionDep, session_id: UUID
) -> list[AdminSessionMessage]:
    return [
        AdminSessionMessage.model_validate(m.model_dump())
        for m in messages_for(
            session, store.get_session(session, principal, session_id)
        )
    ]


@router.post("/sessions/{session_id}/turns")
def turn(
    principal: OrgAdminDep, session: SessionDep, session_id: UUID, body: AdminTurnInput
) -> AdminTurnResponse:
    return core.turn(session, principal, session_id, body)


@router.get("/availability")
def availability(principal: OrgAdminDep, session: SessionDep) -> Availability:
    return core.availability(session, principal)
