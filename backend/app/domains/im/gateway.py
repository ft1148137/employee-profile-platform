import asyncio
import json
import logging
import re
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import Engine
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, col, select

from app.core.secrets import SecretUnavailable, decrypt_secret
from app.domains.im import binding
from app.domains.im.adapters.base import Adapter, Event, ImError
from app.domains.im.adapters.telegram import Telegram
from app.domains.im.models import ImEvent, ImSetting
from app.domains.im.notifications import send_pending
from app.domains.im.schemas import Platform
from app.domains.org.service import visible_employee

ADAPTERS: dict[Platform, type[Adapter]] = {"telegram": Telegram}
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

    def dispatch(self, event: Event, identity: str, credentials: str) -> str | None:
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
            if added is None or event.text is None or not event.external_chat_id:
                return None
            if not event.private:
                return "请私聊我"
            match = re.fullmatch(r"绑定\s+([0-9]{6})", event.text.strip())
            try:
                if event.text.strip().startswith("绑定"):
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
                employee = visible_employee(db, p, p.employee_id)
                # shortcut: Text conversations land in S11b; only acknowledge the valid binding here.
                return f"已绑定为{employee.display_name}。对话功能稍后开放，请在网页使用内置机器人。"
            except HTTPException:
                return binding.GUIDE

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
                        db.add(setting)
                        db.commit()
                    self.adapters[org_id, platform] = credentials, adapter
                    while True:
                        async for event in adapter.poll():
                            reply = self.dispatch(event, identity, credentials)
                            if reply and self.enabled(org_id, platform, credentials):
                                try:
                                    await adapter.send_text(
                                        event.external_chat_id, reply
                                    )
                                except ImError as error:
                                    if error.code != "recipient_unavailable":
                                        raise
                        delay = 1
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
