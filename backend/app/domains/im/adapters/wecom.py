import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextvars import ContextVar
from datetime import datetime
from typing import Any, ClassVar
from uuid import UUID, uuid4

import httpx
import sentry_sdk
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import WebSocketException

from app.domains.base import utc_now
from app.domains.im.adapters.base import Event, ImError
from app.domains.im.schemas import Platform

HEARTBEAT_INTERVAL = 30
_private_http: ContextVar[bool] = ContextVar("wecom_private_http", default=False)


class _PrivateRequest(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not _private_http.get()


for _name in (
    "httpx",
    "httpcore.http11",
    "httpcore.http2",
    "httpcore.connection",
    "httpcore.proxy",
):
    logging.getLogger(_name).addFilter(_PrivateRequest())


class Wecom:
    platform: ClassVar[Platform] = "wecom"

    def __init__(self, org_id: UUID, credentials: dict[str, str]) -> None:
        self.org_id, self.credentials = org_id, credentials
        self.socket: ClientConnection | None = None
        self.tasks: list[asyncio.Task[None]] = []
        self.pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self.events: asyncio.Queue[Event | ImError] = asyncio.Queue(maxsize=128)
        self.callbacks: dict[str, str] = {}
        self.groups: dict[str, str] = {}
        self.checked_at: datetime | None = None
        self.failure: ImError | None = None
        self.client = httpx.AsyncClient(timeout=5, follow_redirects=False)

    async def close(self) -> None:
        self.checked_at = None
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.socket:
            await self.socket.close()
        await self.client.aclose()

    async def _request(
        self, cmd: str, body: dict[str, Any] | None = None, req_id: str | None = None
    ) -> dict[str, Any]:
        if self.failure:
            raise self.failure
        if self.socket is None:
            raise ImError("network", retryable=True)
        req_id = req_id or uuid4().hex
        future: asyncio.Future[dict[str, Any]] = (
            asyncio.get_running_loop().create_future()
        )
        self.pending[req_id] = future
        try:
            with sentry_sdk.new_scope() as scope:
                scope.set_client(
                    sentry_sdk.Client(dsn="", default_integrations=False, release="")
                )
                await self.socket.send(
                    json.dumps(
                        {"cmd": cmd, "headers": {"req_id": req_id}, "body": body or {}}
                    )
                )
                frame = await asyncio.wait_for(future, 5)
            if frame.get("errcode") != 0:
                if frame.get("errcode") in (45009, 45033):
                    raise ImError("rate_limited", retryable=True)
                raise ImError(
                    "credentials"
                    if cmd == "aibot_subscribe"
                    else "recipient_unavailable"
                )
            return frame
        except TimeoutError:
            raise ImError("timeout", retryable=True) from None
        except WebSocketException, OSError:
            raise ImError("network", retryable=True) from None
        finally:
            self.pending.pop(req_id, None)

    async def _read(self) -> None:
        assert self.socket
        error = ImError("network", retryable=True)
        try:
            async for raw in self.socket:
                frame = json.loads(raw)
                req_id = frame["headers"]["req_id"]
                if not frame.get("cmd"):
                    future = self.pending.get(req_id)
                    if future and not future.done():
                        future.set_result(frame)
                    continue
                body = frame.get("body", {})
                detail = body.get("event", {})
                if detail.get("eventtype") == "disconnected_event":
                    error = ImError("connection_conflict")
                    break
                if frame["cmd"] not in ("aibot_msg_callback", "aibot_event_callback"):
                    continue
                if body.get("aibotid") != self.credentials["bot_id"] or body.get(
                    "compat_info"
                ):
                    continue
                sender = body.get("from", {})
                user = sender.get("userid")
                kind = body.get("chattype")
                if (
                    not isinstance(user, str)
                    or not user
                    or sender.get("corpid")
                    or kind not in ("single", "group")
                ):
                    continue
                private = kind == "single"
                chat = user if private else body.get("chatid")
                if (
                    not isinstance(chat, str)
                    or not chat
                    or not isinstance(body.get("msgid"), str)
                ):
                    continue
                is_callback = detail.get("eventtype") == "template_card_event"
                text = (
                    body.get("text", {}).get("content")
                    if body.get("msgtype") == "text"
                    else None
                )
                if not isinstance(text, str) and not is_callback:
                    continue
                if not private:
                    self.groups[chat] = req_id
                if is_callback:
                    if not isinstance(detail.get("task_id"), str) or not isinstance(
                        detail.get("event_key"), str
                    ):
                        continue
                    self.callbacks[req_id] = detail["task_id"]
                    # A previous model turn can still be running; acknowledge within five seconds.
                    await self.answer_callback(req_id)
                self.events.put_nowait(
                    Event(
                        self.org_id,
                        "wecom",
                        body["msgid"],
                        user,
                        chat,
                        private,
                        text,
                        req_id if is_callback else None,
                        detail.get("event_key") if is_callback else None,
                        detail.get("task_id") if is_callback else None,
                    )
                )
        except (
            WebSocketException,
            OSError,
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            asyncio.QueueFull,
        ):
            pass
        finally:
            self.checked_at = None
            self.failure = error
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(error)
            # Drop an undelivered event if necessary so disconnects always wake polling.
            if self.events.full():
                self.events.get_nowait()
            self.events.put_nowait(error)

    async def _heartbeat(self) -> None:
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                await self._request("ping")
                self.checked_at = utc_now()
        except ImError as error:
            self.checked_at = None
            await self.events.put(error)

    async def test_connection(self) -> str:
        if self.socket is None:
            try:
                # WebSocket DEBUG logging contains authentication and message frames.
                logger = logging.Logger("wecom.private", level=logging.CRITICAL)
                self.socket = await connect(
                    "wss://openws.work.weixin.qq.com", logger=logger, open_timeout=5
                )
            except TimeoutError:
                raise ImError("timeout", retryable=True) from None
            except WebSocketException, OSError:
                raise ImError("network", retryable=True) from None
            self.tasks.append(asyncio.create_task(self._read()))
            await self._request(
                "aibot_subscribe",
                {
                    "bot_id": self.credentials["bot_id"],
                    "secret": self.credentials["secret"],
                },
            )
            self.checked_at = utc_now()
            self.tasks.append(asyncio.create_task(self._heartbeat()))
        return self.credentials["bot_id"]

    async def poll(self) -> AsyncIterator[Event]:
        try:
            event = await asyncio.wait_for(self.events.get(), 20)
        except TimeoutError:
            return
        if isinstance(event, ImError):
            raise event
        yield event

    async def send_text(self, external_chat_id: str, text: str) -> None:
        group_req = self.groups.pop(external_chat_id, None)
        if group_req:
            await self._request(
                "aibot_respond_msg",
                {
                    "msgtype": "stream",
                    "stream": {
                        "id": uuid4().hex,
                        "finish": True,
                        "content": "请私聊我",
                    },
                },
                group_req,
            )
            return
        for start in range(0, len(text), 3000):
            await self._request(
                "aibot_send_msg",
                {
                    "chatid": external_chat_id,
                    "chat_type": 1,
                    "msgtype": "markdown",
                    "markdown": {"content": text[start : start + 3000]},
                },
            )

    async def render(
        self, external_chat_id: str, text: str, buttons: list[tuple[str, str]]
    ) -> str:
        task_id = uuid4().hex
        # shortcut: Use the web preview beyond this conservative card budget; verify larger cards with a real application.
        if len(text) > 3000 or len(text.encode()) > 8000:
            await self.send_text(
                external_chat_id, "内容较长，请在网页查看完整内容并操作。"
            )
            return task_id
        card: dict[str, Any] = {
            "card_type": "button_interaction" if buttons else "text_notice",
            "task_id": task_id,
            "main_title": {"title": "员工画像助手"},
            "sub_title_text": text,
        }
        if buttons:
            card["button_list"] = [
                {"text": label, "key": token, "type": 1} for label, token in buttons[:3]
            ]
        await self._request(
            "aibot_send_msg",
            {
                "chatid": external_chat_id,
                "chat_type": 1,
                "msgtype": "template_card",
                "template_card": card,
            },
        )
        return task_id

    async def answer_callback(self, callback_id: str) -> None:
        task_id = self.callbacks.pop(callback_id, None)
        if task_id and self.socket:
            # The reader cannot wait for its own response; the later gateway call is a no-op.
            await self.socket.send(
                json.dumps(
                    {
                        "cmd": "aibot_respond_update_msg",
                        "headers": {"req_id": callback_id},
                        "body": {
                            "response_type": "update_template_card",
                            "template_card": {
                                "card_type": "text_notice",
                                "task_id": task_id,
                                "main_title": {"title": "请在下方查看处理结果"},
                            },
                        },
                    }
                )
            )

    async def clear_buttons(self, external_chat_id: str, message_id: str) -> None:
        # The immediate callback response already replaces the original card.
        pass

    async def enterprise_email(self, user: str) -> str | None:
        if not self.credentials.get("corp_id") or not self.credentials.get(
            "contact_secret"
        ):
            return None
        private = _private_http.set(True)
        try:
            with sentry_sdk.new_scope() as scope:
                scope.set_client(
                    sentry_sdk.Client(dsn="", default_integrations=False, release="")
                )
                response = await self.client.get(
                    "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
                    params={
                        "corpid": self.credentials["corp_id"],
                        "corpsecret": self.credentials["contact_secret"],
                    },
                )
                token = response.json().get("access_token")
                if response.status_code != 200 or not isinstance(token, str):
                    return None
                params = {"access_token": token, "userid": user}
                response = await self.client.get(
                    "https://qyapi.weixin.qq.com/cgi-bin/user/get", params=params
                )
                data = response.json()
                if data.get("errcode"):
                    converted = await self.client.post(
                        "https://qyapi.weixin.qq.com/cgi-bin/batch/openuserid_to_userid",
                        params={"access_token": token},
                        json={"open_userid_list": [user]},
                    )
                    matches = converted.json().get("userid_list", [])
                    if (
                        converted.status_code != 200
                        or len(matches) != 1
                        or matches[0].get("open_userid") != user
                    ):
                        return None
                    params["userid"] = matches[0]["userid"]
                    response = await self.client.get(
                        "https://qyapi.weixin.qq.com/cgi-bin/user/get", params=params
                    )
                    data = response.json()
                email = data.get("biz_mail")
                return (
                    email
                    if response.status_code == 200
                    and data.get("errcode") == 0
                    and isinstance(email, str)
                    and "@" in email
                    else None
                )
        except httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError:
            return None
        finally:
            _private_http.reset(private)
