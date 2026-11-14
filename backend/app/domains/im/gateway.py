import asyncio
import json
import logging
import re
from collections.abc import Callable
from contextlib import suppress
from datetime import timedelta
from functools import partial
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import Engine
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, col, select

from app.core.secrets import SecretUnavailable, decrypt_secret
from app.domains.base import utc_now
from app.domains.bot.attachments import AttachmentError
from app.domains.im import actions, binding
from app.domains.im.adapters.base import Adapter, Event, ImError
from app.domains.im.adapters.feishu import Feishu
from app.domains.im.adapters.telegram import Telegram
from app.domains.im.adapters.wecom import Wecom
from app.domains.im.models import ImEvent, ImSetting
from app.domains.im.notifications import send_pending
from app.domains.im.schemas import Platform

ADAPTERS: dict[Platform, type[Adapter]] = {
    "telegram": Telegram,
    "wecom": Wecom,
    "feishu": Feishu,
}
logger = logging.getLogger(__name__)


class Gateway:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.tasks: dict[tuple[UUID, Platform], tuple[str, asyncio.Task[None]]] = {}
        self.identities: dict[tuple[Platform, str], UUID] = {}
        self.adapters: dict[tuple[UUID, Platform], tuple[str, Adapter]] = {}

    def enabled(self, org_id: UUID, platform: Platform, credentials: str) -> bool:
        with Session(self.engine) as db:
            row = db.get(ImSetting, (org_id, platform))
            return bool(row and row.enabled and row.credentials == credentials)

    def dispatch(
        self,
        event: Event,
        identity: str,
        credentials: str,
        on_started: Callable[[], None] | None = None,
    ) -> str | actions.Reply | None:
        if not self.enabled(event.org_id, event.platform, credentials):
            return None
        with Session(self.engine) as db:
            added = (
                db.connection()
                .execute(
                    insert(ImEvent)
                    .values(
                        org_id=event.org_id,
                        platform=event.platform,
                        event_id=f"{identity}:{event.event_id}",
                    )
                    .on_conflict_do_nothing()
                    .returning(col(ImEvent.event_id))
                )
                .first()
            )
            # shortcut: Receipt is at most once; revisit if crash-safe reply delivery is required.
            db.commit()
            if (
                added is None
                or (
                    event.text is None
                    and not event.callback_id
                    and not event.attachment
                    and not event.attachment_error
                )
                or not event.external_chat_id
            ):
                return None
            if not event.private:
                return "请私聊我"
            if event.attachment_error:
                return event.attachment_error
            text = event.text or ""
            match = re.fullmatch(r"绑定\s+([0-9]{6})", text.strip())
            try:
                if text.strip().startswith("绑定") and not event.callback_id:
                    ok = binding.bind(
                        db,
                        event.org_id,
                        event.platform,
                        event.external_user_id,
                        event.external_chat_id,
                        identity,
                        match[1] if match else "",
                    )
                    return "绑定成功，请回网页查看或解绑。" if ok else binding.GUIDE
                p = binding.current_binding(
                    db,
                    event.org_id,
                    event.platform,
                    event.external_user_id,
                    event.external_chat_id,
                    identity,
                )
                if p is None or p.employee_id is None:
                    return binding.GUIDE
                setting = db.get(ImSetting, (event.org_id, event.platform))
                if not setting or setting.bot_identity != identity:
                    return binding.GUIDE
                if event.callback_id:
                    return actions.callback(db, p, event, identity)
                return actions.text(db, p, event, identity, on_started=on_started)
            except AttachmentError as error:
                db.rollback()
                return str(error.detail)
            except HTTPException:
                db.rollback()
                return (
                    "操作未完成，请重新查看后再试。"
                    if event.callback_id
                    else binding.GUIDE
                )
            except ValidationError:
                db.rollback()
                return "请输入不超过一万字的文本。"

    async def handle(
        self, event: Event, identity: str, credentials: str, adapter: Adapter
    ) -> None:
        if event.callback_id:
            await adapter.answer_callback(event.callback_id)
        if (
            isinstance(adapter, (Feishu, Wecom))
            and event.private
            and (event.text is not None or event.attachment is not None)
            and not (event.text or "").strip().startswith("绑定")
            and self.enabled(event.org_id, event.platform, credentials)
        ):
            with Session(self.engine) as db:
                wanted = binding.wants_email(db, event, identity)
            if wanted:
                email = await adapter.enterprise_email(event.external_user_id)
                with Session(self.engine) as db:
                    if email and binding.bind_email(
                        db, event, identity, credentials, email
                    ):
                        await adapter.send_text(
                            event.external_chat_id,
                            "已按企业邮箱绑定，可以在网页的个人设置中解绑。请继续发送消息。",
                        )
                        return
        loop = asyncio.get_running_loop()
        reminder: asyncio.Task[None] | None = None
        finished = False

        async def remind() -> None:
            await asyncio.sleep(30)
            try:
                if self.enabled(event.org_id, event.platform, credentials):
                    await adapter.send_text(
                        event.external_chat_id, "还在处理，请再等一会儿"
                    )
            except Exception:
                logger.warning("IM processing reminder failed")

        async def acknowledge() -> None:
            nonlocal reminder
            if finished or not self.enabled(event.org_id, event.platform, credentials):
                return
            reminder = asyncio.create_task(remind())
            await adapter.send_text(event.external_chat_id, "收到，正在处理，请稍等")

        def on_started() -> None:
            asyncio.run_coroutine_threadsafe(acknowledge(), loop).result()

        try:
            # Model turns are synchronous; keep polling tasks and setting refresh responsive.
            reply = await asyncio.to_thread(
                self.dispatch, event, identity, credentials, on_started
            )
            if not reply or not self.enabled(event.org_id, event.platform, credentials):
                return
            if isinstance(reply, str):
                await adapter.send_text(event.external_chat_id, reply)
                return
            with Session(self.engine) as db:
                p = binding.current_binding(
                    db,
                    event.org_id,
                    event.platform,
                    event.external_user_id,
                    event.external_chat_id,
                    identity,
                )
                if p is None:
                    return
                if reply.clear_buttons and event.message_id:
                    try:
                        await adapter.clear_buttons(
                            event.external_chat_id, event.message_id
                        )
                    except ImError as error:
                        if error.code != "recipient_unavailable":
                            raise
                await actions.deliver(db, p, event, identity, reply, adapter)
        finally:
            finished = True
            if reminder:
                reminder.cancel()
                with suppress(asyncio.CancelledError):
                    await reminder

    async def poll(self, adapter: Adapter, identity: str, credentials: str) -> None:
        slots = asyncio.Semaphore(4)
        pending: dict[str, asyncio.Task[None]] = {}
        heartbeat_written_at = utc_now()

        async def handle(event: Event, previous: asyncio.Task[None] | None) -> None:
            if previous:
                await previous
            async with slots:
                try:
                    await self.handle(event, identity, credentials, adapter)
                except ImError as error:
                    if error.code != "recipient_unavailable":
                        raise

        def finished(user: str, task: asyncio.Task[None]) -> None:
            if pending.get(user) is task:
                del pending[user]

        try:
            async with asyncio.TaskGroup() as group:
                while True:
                    async for event in adapter.poll():
                        user = event.external_user_id
                        task = group.create_task(handle(event, pending.get(user)))
                        pending[user] = task
                        task.add_done_callback(partial(finished, user))
                    if isinstance(
                        adapter, Wecom
                    ) and utc_now() - heartbeat_written_at >= timedelta(minutes=1):
                        with Session(self.engine) as db:
                            setting = db.get(ImSetting, (adapter.org_id, "wecom"))
                            if setting and setting.credentials == credentials:
                                setting.connection_checked_at = adapter.checked_at
                                db.add(setting)
                                db.commit()
                                heartbeat_written_at = utc_now()
        except ExceptionGroup as errors:
            # TaskGroup joins all children before preserving the existing reconnect path.
            raise errors.exceptions[0] from None

    async def connect(self, org_id: UUID, platform: Platform, credentials: str) -> None:
        delay = 1
        identity = None
        adapter = None
        try:
            while True:
                try:
                    adapter = ADAPTERS[platform](
                        org_id, json.loads(decrypt_secret(credentials))
                    )
                    if isinstance(adapter, Wecom):
                        identity = adapter.credentials["bot_id"]
                        if self.identities.get((platform, identity), org_id) != org_id:
                            raise ImError("bot_already_used")
                        self.identities[platform, identity] = org_id
                    identity = await adapter.test_connection()
                    key = platform, identity
                    owner = self.identities.get(key)
                    if owner is not None and owner != org_id:
                        raise ImError("bot_already_used")
                    self.identities[key] = org_id
                    with Session(self.engine) as db:
                        setting = db.get(ImSetting, (org_id, platform))
                        if (
                            not setting
                            or not setting.enabled
                            or setting.credentials != credentials
                        ):
                            return
                        setting.bot_identity = identity
                        if isinstance(adapter, Wecom):
                            setting.connection_checked_at = adapter.checked_at
                        db.add(setting)
                        db.commit()
                    self.adapters[org_id, platform] = credentials, adapter
                    delay = 1
                    await self.poll(adapter, identity, credentials)
                except (
                    ImError,
                    SecretUnavailable,
                    ValueError,
                    KeyError,
                    SQLAlchemyError,
                ) as error:
                    code = (
                        error.code
                        if isinstance(error, ImError)
                        else (
                            "storage"
                            if isinstance(error, SQLAlchemyError)
                            else "invalid_credentials"
                        )
                    )
                    logger.warning(
                        "IM connection stopped: org=%s platform=%s code=%s",
                        org_id,
                        platform,
                        code,
                    )
                    if not isinstance(error, SQLAlchemyError) and (
                        not isinstance(error, ImError) or not error.retryable
                    ):
                        return
                finally:
                    self.adapters.pop((org_id, platform), None)
                    if adapter:
                        await adapter.close()
                        adapter = None
                    if platform == "wecom":
                        with Session(self.engine) as db:
                            setting = db.get(ImSetting, (org_id, platform))
                            if setting and setting.credentials == credentials:
                                setting.connection_checked_at = None
                                db.add(setting)
                                db.commit()
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)
        finally:
            if identity and self.identities.get((platform, identity)) == org_id:
                del self.identities[platform, identity]

    async def refresh(self) -> None:
        with Session(self.engine) as db:
            rows = db.exec(select(ImSetting).where(ImSetting.enabled == True)).all()  # noqa: E712
            wanted = {
                (r.org_id, r.platform): r.credentials
                for r in rows
                if r.platform in ADAPTERS and r.credentials
            }
        for key, (credentials, task) in list(self.tasks.items()):
            if wanted.get(key) != credentials:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                del self.tasks[key]
        for (org_id, platform), credentials in wanted.items():
            # Failed credentials stay stopped until settings actually change.
            if (org_id, platform) not in self.tasks:
                task = asyncio.create_task(self.connect(org_id, platform, credentials))
                self.tasks[org_id, platform] = credentials, task

    async def run(self) -> None:
        try:
            while True:
                await self.refresh()
                try:
                    await send_pending(self.engine, self.adapters)
                except SQLAlchemyError:
                    logger.warning("IM notification delivery stopped: code=storage")
                await asyncio.sleep(10)
        finally:
            for _, task in self.tasks.values():
                task.cancel()
            await asyncio.gather(
                *(t for _, t in self.tasks.values()), return_exceptions=True
            )
