from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.deps import SessionDep
from app.api.secret_safe import SecretSafeRoute
from app.authz.deps import OrgAdminDep, OrgPrincipalDep
from app.domains.admin_assistant import change_set, core, store
from app.domains.admin_assistant.registry import ConnectionTestResult
from app.domains.admin_assistant.schemas import (
    AdminSessionMessage,
    AdminTurnInput,
    AdminTurnResponse,
    ApplyChanges,
    Availability,
    ChangeSetPublic,
    SecretInput,
    TestConnection,
)
from app.domains.bot.schemas import SessionCreated, TextMessage
from app.domains.bot.store import BotSessionNotFound, messages_for


def require_admin(principal: OrgPrincipalDep) -> None:
    # Conceal private assistant sessions from non-administrators as well.
    if not principal.is_org_admin:
        raise BotSessionNotFound()


router = APIRouter(
    route_class=SecretSafeRoute,
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


change_router = APIRouter(
    route_class=SecretSafeRoute,
    prefix="/orgs/{org_id}/admin-assistant/change-sets",
    tags=["admin-assistant"],
    dependencies=[Depends(require_admin)],
)


@change_router.get("")
def list_change_sets(
    principal: OrgAdminDep,
    session: SessionDep,
    function: str,
    status: Literal["draft"],
) -> list[ChangeSetPublic]:
    return change_set.list_drafts(session, principal, function, status)


@change_router.put("/{id}/secrets/{field}")
def save_change_set_secret(
    principal: OrgAdminDep, session: SessionDep, id: UUID, field: str, body: SecretInput
) -> TextMessage:
    change_set.save_secret(session, principal, id, field, body.value.get_secret_value())
    return TextMessage(text="已填写")


@change_router.get("/{id}")
def get_change_set(
    principal: OrgAdminDep, session: SessionDep, id: UUID
) -> ChangeSetPublic:
    return change_set.public(change_set.get(session, principal, id))


@change_router.post("/{id}/apply")
def apply_change_set(
    principal: OrgAdminDep, session: SessionDep, id: UUID, body: ApplyChanges
) -> ChangeSetPublic:
    return change_set.apply(session, principal, id, body.revision)


@change_router.post("/{id}/test")
async def test_change_set(
    principal: OrgAdminDep, session: SessionDep, id: UUID, body: TestConnection
) -> ConnectionTestResult:
    return await change_set.test_connection(session, principal, id, body.item)


@change_router.post("/{id}/discard")
def discard_change_set(
    principal: OrgAdminDep, session: SessionDep, id: UUID
) -> ChangeSetPublic:
    return change_set.discard(session, principal, id)


@change_router.post("/{id}/undo")
def undo_change_set(
    principal: OrgAdminDep, session: SessionDep, id: UUID
) -> ChangeSetPublic:
    return change_set.undo(session, principal, id)
