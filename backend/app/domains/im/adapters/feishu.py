import asyncio
import json
import logging
import multiprocessing
import time
from collections.abc import AsyncIterator
from dataclasses import asdict
from multiprocessing.process import BaseProcess
from multiprocessing.queues import Queue
from queue import Empty
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx
import sentry_sdk
from pydantic import JsonValue, TypeAdapter, ValidationError

from app.domains.im.adapters.base import Event, ImError


def _listen(credentials: dict[str, str], events: Queue[tuple[str, str]]) -> None:
    # The SDK owns a global loop and logs payloads/connection URLs, so isolate it.
    logging.disable(logging.CRITICAL)
    import lark_oapi as lark  # type: ignore[import-untyped]
    from lark_oapi.event.callback.model.p2_card_action_trigger import (  # type: ignore[import-untyped]
        P2CardActionTriggerResponse,
    )
    from lark_oapi.ws.exception import ClientException  # type: ignore[import-untyped]

    def receive(data: object) -> None:
        payload = lark.JSON.marshal(data)
        if payload:
            events.put_nowait(("event", payload))

    def callback(data: object) -> P2CardActionTriggerResponse:
        receive(data)
        return P2CardActionTriggerResponse({})

    handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(receive)
        .register_p2_card_action_trigger(callback)
        .build()
    )
    try:
        lark.ws.Client(
            credentials["app_id"], credentials["app_secret"], event_handler=handler
        ).start()
    except ClientException:
        events.put_nowait(("error", "credentials"))
    except Exception:
        # SDK exceptions can contain upstream bodies and authenticated URLs.
        events.put_nowait(("error", "network"))
    finally:
        events.close()
        events.join_thread()


class Feishu:
    def __init__(self, org_id: UUID, credentials: dict[str, str]) -> None:
        self.org_id, self.credentials = org_id, credentials
        self.client = httpx.AsyncClient(
            base_url="https://open.feishu.cn/open-apis/",
            timeout=10,
            follow_redirects=False,
        )
        self.token = ""
        self.expires = 0.0
        self.process: BaseProcess | None = None
        self.events: Queue[tuple[str, str]] = multiprocessing.get_context(
            "spawn"
        ).Queue(256)

    async def close(self) -> None:
        if self.process:
            if self.process.is_alive():
                self.process.terminate()
            await asyncio.to_thread(self.process.join)
            self.process.close()
            self.process = None
        self.events.close()
        await self.client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        body: dict[str, JsonValue] | None = None,
        *,
        authenticated: bool = True,
    ) -> dict[str, Any]:
        if authenticated:
            await self._authenticate()
        try:
            with sentry_sdk.new_scope() as scope:
                scope.set_client(
                    sentry_sdk.Client(dsn="", default_integrations=False, release="")
                )
                response = await self.client.request(
                    method,
                    path,
                    json=body,
                    headers={"Authorization": f"Bearer {self.token}"}
                    if authenticated
                    else {},
                )
            if response.status_code in (401, 403):
                raise ImError("credentials")
            if response.status_code == 429:
                raise ImError("rate_limited", retryable=True)
            if response.status_code != 200:
                raise ImError("unavailable", retryable=True)
            data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get("code"), int):
                raise ImError("invalid_response")
            code = data["code"]
            if code in (99991663, 99991664, 99991668):
                self.token, self.expires = "", 0
                raise ImError("credentials", retryable=True)
            if code != 0:
                raise ImError(
                    "credentials" if not authenticated else "recipient_unavailable"
                )
            return data
        except httpx.TimeoutException:
            raise ImError("timeout", retryable=True) from None
        except httpx.HTTPError:
            raise ImError("network", retryable=True) from None
        except ValueError, TypeError:
            raise ImError("invalid_response") from None

    async def _authenticate(self) -> None:
        if time.monotonic() < self.expires:
            return
        data = await self._request(
            "POST",
            "auth/v3/tenant_access_token/internal",
            {
                "app_id": self.credentials["app_id"],
                "app_secret": self.credentials["app_secret"],
            },
            authenticated=False,
        )
        if not isinstance(data.get("tenant_access_token"), str) or not isinstance(
            data.get("expire"), int
        ):
            raise ImError("invalid_response")
        self.token = data["tenant_access_token"]
        self.expires = time.monotonic() + max(0, data["expire"] - 60)

    async def test_connection(self) -> str:
        await self._authenticate()
        return self.credentials["app_id"]

    async def enterprise_email(self, external_user_id: str) -> str | None:
        try:
            data = await self._request(
                "GET",
                f"contact/v3/users/{quote(external_user_id, safe='')}?user_id_type=open_id",
            )
            email = data["data"]["user"].get("enterprise_email")
            return email if isinstance(email, str) and email.strip() else None
        except ImError, KeyError, TypeError, AttributeError:
            return None

    async def _event(self, payload: str) -> Event | None:
        try:
            data = json.loads(payload)
            header, event = data["header"], data["event"]
            if header["event_type"] == "im.message.receive_v1":
                message, sender = event["message"], event["sender"]
                if sender["sender_type"] != "user" or message["message_type"] != "text":
                    return None
                text = json.loads(message["content"])["text"]
                if not isinstance(text, str):
                    return None
                result = Event(
                    self.org_id,
                    "feishu",
                    header["event_id"],
                    sender["sender_id"]["open_id"],
                    message["chat_id"],
                    message["chat_type"] == "p2p",
                    text,
                )
            elif header["event_type"] == "card.action.trigger":
                context = event["context"]
                chat_id = context["open_chat_id"]
                # Callbacks have no chat_type; ask the platform rather than trusting the card.
                chat = await self._request(
                    "GET", f"im/v1/chats/{quote(chat_id, safe='')}"
                )
                result = Event(
                    self.org_id,
                    "feishu",
                    header["event_id"],
                    event["operator"]["open_id"],
                    chat_id,
                    chat["data"]["chat_mode"] == "p2p",
                    callback_id=header["event_id"],
                    token=event["action"]["value"]["token"],
                    message_id=context["open_message_id"],
                )
            else:
                return None
            return TypeAdapter(Event).validate_python(asdict(result))
        except (
            ImError,
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            ValidationError,
        ):
            return None
        return None

    async def poll(self) -> AsyncIterator[Event]:
        if self.process is None:
            self.process = multiprocessing.get_context("spawn").Process(
                target=_listen, args=(self.credentials, self.events), daemon=True
            )
            self.process.start()
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except Empty:
                if not self.process.is_alive():
                    raise ImError("network", retryable=True)
                await asyncio.sleep(0.1)
                continue
            if kind == "error":
                raise ImError(payload, retryable=payload == "network")
            event = await self._event(payload)
            if event:
                yield event

    async def send_text(self, external_chat_id: str, text: str) -> None:
        for start in range(0, len(text), 3000):
            await self._request(
                "POST",
                "im/v1/messages?receive_id_type=chat_id",
                {
                    "receive_id": external_chat_id,
                    "msg_type": "text",
                    "content": json.dumps(
                        {"text": text[start : start + 3000]}, ensure_ascii=False
                    ),
                },
            )

    async def render(
        self, external_chat_id: str, text: str, buttons: list[tuple[str, str]]
    ) -> str:
        # shortcut: Conservative card budgets; verify hard limits with a real test app.
        if len(text) > 3000:
            text, buttons = "内容较长，请在网页查看完整内容并操作。", []
        elements: list[dict[str, JsonValue]] = [
            {"tag": "div", "text": {"tag": "plain_text", "content": text}}
        ]
        if buttons:
            elements.append(
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": label},
                            "value": {"token": token},
                        }
                        for label, token in buttons[:3]
                    ],
                }
            )
        content = json.dumps({"elements": elements}, ensure_ascii=False)
        if len(content.encode()) > 10 * 1024:
            return await self.render(
                external_chat_id, "内容较长，请在网页查看完整内容并操作。", []
            )
        data = await self._request(
            "POST",
            "im/v1/messages?receive_id_type=chat_id",
            {
                "receive_id": external_chat_id,
                "msg_type": "interactive",
                "content": content,
            },
        )
        try:
            message_id = data["data"]["message_id"]
            if isinstance(message_id, str) and message_id:
                return message_id
        except KeyError, TypeError:
            pass
        raise ImError("invalid_response")

    async def answer_callback(self, callback_id: str) -> None:
        # SDK callbacks acknowledge receipt immediately, before the model turn.
        pass

    async def clear_buttons(self, external_chat_id: str, message_id: str) -> None:
        await self._request(
            "PATCH",
            f"im/v1/messages/{quote(message_id, safe='')}",
            {
                "content": json.dumps(
                    {
                        "elements": [
                            {
                                "tag": "div",
                                "text": {
                                    "tag": "plain_text",
                                    "content": "已处理，请查看后续消息。",
                                },
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
            },
        )
