from uuid import UUID

from fastapi import APIRouter, Request, Response, UploadFile
from fastapi.responses import StreamingResponse

from app.api.deps import SessionDep
from app.authz.deps import OrgPrincipalDep
from app.domains.bot import attachments, core, store, streaming
from app.domains.bot.schemas import (
    Action,
    SessionCreated,
    SessionMessage,
    TurnInput,
    TurnResponse,
)

router = APIRouter(prefix="/bot/sessions", tags=["bot"])


@router.get("")
def list_sessions(principal: OrgPrincipalDep, session: SessionDep) -> store.SessionList:
    return store.list_sessions(session, principal)


@router.post("", status_code=201)
def create_session(principal: OrgPrincipalDep, session: SessionDep) -> SessionCreated:
    return SessionCreated(session_id=store.create_session(session, principal).id)


@router.get("/{session_id}/messages", response_model_exclude_unset=True)
def list_messages(
    principal: OrgPrincipalDep, session: SessionDep, session_id: UUID
) -> list[SessionMessage]:
    row = store.get_session(session, principal, session_id)
    messages = store.messages_for(session, row)
    if not messages and row.turn_count:
        raise store.BotSessionNotFound()
    return [SessionMessage.model_validate(message.model_dump()) for message in messages]


@router.post(
    "/{session_id}/turns",
    status_code=200,
    response_class=streaming.TurnStreamingResponse,
    response_model=streaming.TextEvent
    | streaming.ToolEvent
    | streaming.FinalEvent[TurnResponse],
)
def turn(
    principal: OrgPrincipalDep,
    session: SessionDep,
    session_id: UUID,
    body: TurnInput,
    request: Request,
) -> StreamingResponse:
    store.get_session(session, principal, session_id)
    attachments.prepare(
        session, principal, "bot", session_id, body.attachment_ids, body.text
    )
    session.close()  # The worker owns its Session; release the preflight connection.
    return streaming.response(
        lambda db, stream: core.turn(
            db,
            principal,
            session_id,
            body.text,
            attachment_ids=body.attachment_ids,
            stream=stream,
        ),
        request,
    )


@router.post("/{session_id}/actions")
def act(
    principal: OrgPrincipalDep,
    session: SessionDep,
    session_id: UUID,
    body: Action,
) -> TurnResponse:
    return core.act(session, principal, session_id, body)


@router.post("/{session_id}/attachments")
def upload_attachment(
    principal: OrgPrincipalDep, session: SessionDep, session_id: UUID, file: UploadFile
) -> attachments.AttachmentPublic:
    return attachments.upload(session, principal, "bot", session_id, file)


@router.get(
    "/{session_id}/attachments/{attachment_id}",
    response_class=Response,
    responses={
        200: {
            "content": {
                "application/octet-stream": {
                    "schema": {"type": "string", "format": "binary"}
                }
            }
        }
    },
)
def download_attachment(
    principal: OrgPrincipalDep,
    session: SessionDep,
    session_id: UUID,
    attachment_id: UUID,
) -> Response:
    return attachments.download(session, principal, "bot", session_id, attachment_id)
