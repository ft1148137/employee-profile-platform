import asyncio
import json
import logging
from collections.abc import AsyncGenerator, Callable
from threading import Event
from typing import Literal
from uuid import uuid4

import anyio
import httpx
from fastapi import Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, JsonValue
from sqlmodel import Session
from starlette.types import Send

from app.core.db import engine
from app.domains.model.pi_client import TurnStream, cancel_run

logger = logging.getLogger(__name__)


class TextEvent(BaseModel):
    type: Literal["text"]
    text: str


class ToolEvent(BaseModel):
    type: Literal["tool"]
    name: str
    phase: Literal["start", "end"]
    ok: bool


class FinalEvent[T: BaseModel](BaseModel):
    type: Literal["final"]
    response: T


class TurnStreamingResponse(StreamingResponse):
    media_type = "application/x-ndjson"

    def __init__(self, content: AsyncGenerator[str]) -> None:
        super().__init__(
            content, headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
        )
        self.events = content

    async def stream_response(self, send: Send) -> None:
        try:
            await super().stream_response(send)
        finally:
            await self.events.aclose()


def response(
    run: Callable[[Session, TurnStream], BaseModel], request: Request
) -> StreamingResponse:
    async def events() -> AsyncGenerator[str]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict[str, JsonValue] | None] = asyncio.Queue()

        def emit(event: dict[str, JsonValue] | None) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, event)

        stream = TurnStream(
            run_id=str(uuid4()),
            on_text=lambda delta: emit({"type": "text", "text": delta}),
            on_tool=lambda name, phase, ok: emit(
                {"type": "tool", "name": name, "phase": phase, "ok": ok}
            ),
            cancelled=Event(),
        )

        def work() -> None:
            try:
                with Session(engine) as db:
                    result = run(db, stream)
                if not stream.cancelled.is_set():
                    emit({"type": "final", "response": result.model_dump(mode="json")})
            finally:
                emit(None)

        worker = asyncio.create_task(asyncio.to_thread(work))
        finished = False
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.1)
                except TimeoutError:
                    if await request.is_disconnected():
                        break
                    continue
                if event is None:
                    await asyncio.shield(worker)
                    finished = True
                    break
                yield json.dumps(event, ensure_ascii=False) + "\n"
        finally:
            # Starlette cancels this generator on disconnect; join before releasing it.
            with anyio.CancelScope(shield=True):
                if not finished:
                    stream.cancelled.set()
                    try:
                        await asyncio.to_thread(cancel_run, stream.run_id)
                    except httpx.HTTPError:
                        logger.warning(
                            "Pi cancellation failed; waiting for turn cleanup"
                        )
                await asyncio.shield(worker)

    return TurnStreamingResponse(events())
