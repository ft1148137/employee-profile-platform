from uuid import UUID

from fastapi import APIRouter

from app.api.deps import SessionDep
from app.authz.deps import OrgPrincipalDep
from app.domains.bot import core, store
from app.domains.bot.schemas import Action, SessionCreated, TurnInput, TurnResponse
from app.domains.bot.store import MessagePublic

router = APIRouter(prefix="/bot/sessions", tags=["bot"])


@router.post("", status_code=201)
def create_session(principal: OrgPrincipalDep, session: SessionDep) -> SessionCreated:
    return SessionCreated(session_id=store.create_session(session, principal).id)


@router.get("/{session_id}/messages")
def list_messages(
    principal: OrgPrincipalDep, session: SessionDep, session_id: UUID
) -> list[MessagePublic]:
    return store.list_messages(session, principal, session_id)


@router.post("/{session_id}/turns")
def turn(
    principal: OrgPrincipalDep, session: SessionDep, session_id: UUID, body: TurnInput
) -> TurnResponse:
    return core.turn(session, principal, session_id, body.text)


@router.post("/{session_id}/actions")
def act(
    principal: OrgPrincipalDep, session: SessionDep, session_id: UUID, body: Action
) -> TurnResponse:
    return core.act(session, principal, session_id, body)
