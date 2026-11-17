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
from app.domains.bot import delivery
from app.domains.bot.admission import (
    BotBusy,
    BotDraftingPriority,
    BotQuotaPaused,
    BotRateLimited,
)
from app.domains.bot.presentation import DEADLINE_MESSAGE, retry_time
from app.domains.bot.schemas import TurnResponse
from app.domains.bot.store import BotSessionNotFound
from app.domains.model.client import (
    ModelRateLimited,
    ModelServiceError,
    ModelUnavailable,
)
from app.domains.model.pi_client import PiSessionInvalid, TurnStream, cancel_run

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


class ErrorEvent(BaseModel):
    type: Literal["error"]
    message: str


def _error_message(error: Exception) -> str:
    if isinstance(error, (BotSessionNotFound, PiSessionInvalid)):
        return "会话已过期，请重新开始对话"
    if isinstance(error, BotRateLimited):
        return f"对话次数已达上限，请在 {retry_time(error.retry_at)} 后重试"
    if isinstance(error, (BotQuotaPaused, ModelRateLimited)):
        return "模型额度暂时用完，请稍后重试"
    if isinstance(error, BotDraftingPriority):
        return "正在起草画像，请稍后再试"
    if isinstance(error, BotBusy):
        return "系统正忙，请稍后重试"
    if isinstance(error, (TimeoutError, httpx.TimeoutException)):
        return "对话处理超时，请稍后重试"
    if isinstance(error, ModelUnavailable):
        if error.code == "deadline":
            return DEADLINE_MESSAGE
        if isinstance(error, ModelServiceError):
            return str(error)
        return "模型服务暂时不可用，请稍后重试"
    return "对话暂时出错，请稍后重试"


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
        queue: asyncio.Queue[dict[str, JsonValue] | BaseModel | None] = asyncio.Queue()

        def emit(event: dict[str, JsonValue] | BaseModel | None) -> None:
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
                    emit(result)
            except Exception as error:
                logger.warning(
                    "Streaming turn failed error_type=%s", type(error).__name__
                )
                if not stream.cancelled.is_set():
                    emit({"type": "error", "message": _error_message(error)})
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
                if isinstance(event, TurnResponse):
                    # Hold the scope lock until StreamingResponse has awaited send.
                    with Session(engine) as db:
                        async with delivery.in_thread(
                            delivery.guard(db, event)
                        ) as result:
                            yield (
                                FinalEvent(
                                    type="final", response=result
                                ).model_dump_json()
                                + "\n"
                            )
                elif isinstance(event, BaseModel):
                    yield (
                        FinalEvent(type="final", response=event).model_dump_json()
                        + "\n"
                    )
                else:
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
