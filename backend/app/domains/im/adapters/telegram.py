import asyncio
import logging
import re
from collections.abc import AsyncIterator
from uuid import UUID

import httpx
import sentry_sdk
from pydantic import BaseModel, Field, JsonValue, TypeAdapter, ValidationError

from app.domains.im.adapters.base import Event, ImError


class _PrivateRequest(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return re.search(r"/bot[0-9]+:[A-Za-z0-9_-]+", record.getMessage()) is None


# Filters work even when deployment or tests enable DEBUG request logging.
for _name in (
    "httpx",
    "httpcore.http11",
    "httpcore.http2",
    "httpcore.connection",
    "httpcore.proxy",
):
    logging.getLogger(_name).addFilter(_PrivateRequest())


class _User(BaseModel):
    id: int
    is_bot: bool = True


class _Chat(BaseModel):
    id: int
    type: str


class _Message(BaseModel):
    sender: _User | None = Field(default=None, alias="from")
    message_id: int | None = None
    chat: _Chat
    text: str | None = None


class _Callback(BaseModel):
    id: str
    sender: _User = Field(alias="from")
    message: _Message | None = None
    data: str | None = None


class _Update(BaseModel):
    update_id: int
    message: _Message | None = None
    callback_query: _Callback | None = None


class Telegram:
    def __init__(self, org_id: UUID, credentials: dict[str, str]) -> None:
        self.org_id = org_id
        self.client = httpx.AsyncClient(
            base_url=f"https://api.telegram.org/bot{credentials['bot_token']}/",
            timeout=40,
            follow_redirects=False,
        )
        self.offset = 0
        self.webhook_checked = False

    async def close(self) -> None:
        await self.client.aclose()

    async def _request(
        self, method: str, body: dict[str, JsonValue] | None = None
    ) -> JsonValue:
        for _ in range(3):
            if self.client.is_closed:
                raise ImError("network", retryable=True)
            try:
                # Telegram embeds credentials in URLs; disable tracing at this boundary.
                with sentry_sdk.new_scope() as scope:
                    scope.set_client(
                        sentry_sdk.Client(
                            dsn="", default_integrations=False, release=""
                        )
                    )
                    response = (
                        await self.client.get(method)
                        if body is None
                        else await self.client.post(method, json=body)
                    )
                if method in (
                    "sendMessage",
                    "editMessageReplyMarkup",
                    "answerCallbackQuery",
                ) and response.status_code in (400, 403):
                    raise ImError("recipient_unavailable")
                if response.status_code in (401, 403, 404):
                    raise ImError("credentials")
                if response.status_code == 409:
                    raise ImError("connection_conflict")
                if response.status_code == 429:
                    data = response.json()
                    retry = int(data.get("parameters", {}).get("retry_after", 1))
                    await asyncio.sleep(max(1, retry))
                    continue
                if response.status_code != 200:
                    raise ImError("unavailable", retryable=True)
                payload = response.json()
                if not isinstance(payload, dict) or payload.get("ok") is not True:
                    raise ImError("invalid_response")
                return TypeAdapter(JsonValue).validate_python(payload.get("result"))
            except httpx.TimeoutException:
                raise ImError("timeout", retryable=True) from None
            except httpx.HTTPError:
                raise ImError("network", retryable=True) from None
            except ValueError, TypeError, AttributeError:
                raise ImError("invalid_response") from None
        raise ImError("rate_limited", retryable=True)

    async def test_connection(self) -> str:
        result = await self._request("getMe")
        if not isinstance(result, dict) or not isinstance(result.get("id"), int):
            raise ImError("invalid_response")
        return str(result["id"])

    async def poll(self) -> AsyncIterator[Event]:
        if not self.webhook_checked:
            webhook = await self._request("getWebhookInfo")
            if not isinstance(webhook, dict) or not isinstance(webhook.get("url"), str):
                raise ImError("invalid_response")
            if webhook["url"]:
                raise ImError("webhook_exists")
            self.webhook_checked = True
        result = await self._request(
            "getUpdates",
            {
                "offset": self.offset,
                "timeout": 25,
                "allowed_updates": ["message", "callback_query"],
            },
        )
        try:
            updates = TypeAdapter(list[_Update]).validate_python(result)
        except ValidationError:
            raise ImError("invalid_response") from None
        for update in updates:
            msg = update.message
            event = Event(self.org_id, "telegram", str(update.update_id))
            if msg and msg.sender and not msg.sender.is_bot:
                private = msg.chat.type == "private"
                if not private or msg.chat.id == msg.sender.id:
                    event = Event(
                        self.org_id,
                        "telegram",
                        str(update.update_id),
                        str(msg.sender.id),
                        str(msg.chat.id),
                        private,
                        msg.text,
                    )
            cb = update.callback_query
            if cb and not cb.sender.is_bot:
                msg = cb.message
                private = bool(
                    msg and msg.chat.type == "private" and msg.chat.id == cb.sender.id
                )
                event = Event(
                    self.org_id,
                    "telegram",
                    str(update.update_id),
                    str(cb.sender.id),
                    str(msg.chat.id) if msg else "",
                    private,
                    callback_id=cb.id,
                    token=cb.data,
                    message_id=str(msg.message_id) if msg and msg.message_id else None,
                )
            yield event
            # The consumer persists im_event before resuming this generator.
            self.offset = update.update_id + 1

    async def send_text(self, external_chat_id: str, text: str) -> None:
        for start in range(0, len(text), 3000):
            await self._request(
                "sendMessage",
                {"chat_id": external_chat_id, "text": text[start : start + 3000]},
            )

    async def answer_callback(self, callback_id: str) -> None:
        await self._request("answerCallbackQuery", {"callback_query_id": callback_id})

    async def clear_buttons(self, external_chat_id: str, message_id: str) -> None:
        await self._request(
            "editMessageReplyMarkup",
            {
                "chat_id": external_chat_id,
                "message_id": int(message_id),
                "reply_markup": {"inline_keyboard": []},
            },
        )

    async def render(
        self, external_chat_id: str, text: str, buttons: list[tuple[str, str]]
    ) -> str:
        message_id = ""
        for start in range(0, len(text), 3000):
            body: dict[str, JsonValue] = {
                "chat_id": external_chat_id,
                "text": text[start : start + 3000],
            }
            if buttons and start + 3000 >= len(text):
                body["reply_markup"] = {
                    "inline_keyboard": [
                        [
                            {"text": label, "callback_data": token}
                            for label, token in buttons[:3]
                        ]
                    ]
                }
            result = await self._request("sendMessage", body)
            if not isinstance(result, dict) or not isinstance(
                result.get("message_id"), int
            ):
                raise ImError("invalid_response")
            message_id = str(result["message_id"])
        return message_id
