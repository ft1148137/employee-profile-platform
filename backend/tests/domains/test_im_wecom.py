import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
import sentry_sdk
from sqlalchemy import Engine, inspect
from sqlmodel import Session, col, select
from websockets.asyncio.server import ServerConnection, serve

from app.core.db import engine
from app.core.secrets import encrypt_secret
from app.domains.base import utc_now
from app.domains.bot import core
from app.domains.change_request.models import ProfileChangeRequest
from app.domains.im import binding, gateway, settings
from app.domains.im.adapters import wecom
from app.domains.im.adapters.base import Adapter, Event, ImError
from app.domains.im.models import ImAction, ImBinding, ImSetting
from app.domains.org.models import Employee, Organization, OrgMembership
from app.models import User
from tests.api.routes.test_employee_requests import payload
from tests.domains.test_bot_core import enable as enable_model
from tests.domains.test_im_gateway import principal
from tests.utils.migrations import migrate
from tests.utils.pi import PiHarness

CREDS = {
    "bot_id": "synthetic-bot",
    "secret": "private-secret",
    "corp_id": "synthetic-corp",
    "contact_secret": "private-contact",
}


@asynccontextmanager
async def server(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[dict[str, Any]]:
    state = {"requests": [], "code": 0, "ping": asyncio.Event()}

    async def handle(socket: ServerConnection) -> None:
        state["socket"] = socket
        async for raw in socket:
            frame = json.loads(raw)
            state["requests"].append(frame)
            await socket.send(
                json.dumps(
                    {
                        "headers": frame["headers"],
                        "errcode": state["code"],
                        "errmsg": "private-upstream-body",
                    }
                )
            )
            if frame["cmd"] == "ping":
                state["ping"].set()

    original = wecom.connect
    async with serve(
        handle,
        "127.0.0.1",
        0,
        logger=logging.Logger("fake.private", level=logging.CRITICAL),
    ) as fake:

        def connect(url: str, **kwargs: Any) -> Any:
            assert url == "wss://openws.work.weixin.qq.com"
            return original(
                f"ws://127.0.0.1:{fake.sockets[0].getsockname()[1]}",
                proxy=None,
                **kwargs,
            )

        monkeypatch.setattr(wecom, "connect", connect)
        yield state


def setup(db: Session, demo: tuple) -> tuple:
    p = principal(db, demo[1]["E02"].org_id, demo[1]["E02"].id)
    row = ImSetting(
        org_id=p.org_id,
        platform="wecom",
        enabled=True,
        bot_identity=CREDS["bot_id"],
        credentials=encrypt_secret(json.dumps(CREDS)),
        updated_by_user_id=p.user_id,
    )
    db.add(row)
    db.commit()
    return p, row


def test_conversation(
    demo: tuple, db: Session, pi_service: PiHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    p, setting = setup(db, demo)
    enable_model(db, p.org_id)
    monkeypatch.setattr(core, "model_config", lambda _: pi_service.config)
    pi_service.replies = [
        {
            "calls": [
                {
                    "name": "draft_update_request",
                    "arguments": {"items": [payload(owner_statement="本人合成经历")]},
                }
            ]
        },
        "请确认预览",
    ]

    async def run() -> None:
        async with server(monkeypatch) as fake:
            adapter = wecom.Wecom(p.org_id, CREDS)
            g = gateway.Gateway(engine)
            try:
                assert await adapter.test_connection() == CREDS["bot_id"]

                async def pump(
                    text: str | None = None,
                    action: ImAction | None = None,
                    group: bool = False,
                ) -> None:
                    body = {
                        "aibotid": CREDS["bot_id"],
                        "msgid": uuid4().hex,
                        "chattype": "group" if group else "single",
                        "chatid": "group",
                        "from": {"userid": "user"},
                        "msgtype": "text",
                        "text": {"content": text},
                    }
                    if action:
                        body.update(
                            msgtype="event",
                            event={
                                "eventtype": "template_card_event",
                                "event_key": action.token,
                                "task_id": action.message_id,
                            },
                        )
                    await fake["socket"].send(
                        json.dumps(
                            {
                                "cmd": "aibot_event_callback"
                                if action
                                else "aibot_msg_callback",
                                "headers": {"req_id": uuid4().hex},
                                "body": body,
                            }
                        )
                    )
                    async for event in adapter.poll():
                        await g.handle(
                            event, CREDS["bot_id"], setting.credentials, adapter
                        )

                await pump("合成群聊", group=True)
                assert fake["requests"][-1]["body"]["stream"]["content"] == "请私聊我"
                # No directory permission: unbound users only receive the binding guide.
                adapter.credentials = {
                    "bot_id": CREDS["bot_id"],
                    "secret": CREDS["secret"],
                }
                await pump("合成未绑定消息")
                assert (
                    fake["requests"][-1]["body"]["markdown"]["content"] == binding.GUIDE
                )
                await pump("绑定 " + binding.create_code(db, p).code)
                await pump("本人合成经历")
                row = db.exec(
                    select(ImAction).where(
                        ImAction.org_id == p.org_id, col(ImAction.consumed_at).is_(None)
                    )
                ).first()
                assert row and row.action["action"] == "confirm_submit"
                card = next(
                    frame["body"]["template_card"]
                    for frame in fake["requests"]
                    if frame["body"].get("template_card", {}).get("task_id")
                    == row.message_id
                )
                assert (
                    card["task_id"] == row.message_id
                    and card["button_list"][0]["key"] == row.token
                )
                request_id = UUID(row.action["request_id"])
                await pump(action=row)
                request = db.get(
                    ProfileChangeRequest, request_id, populate_existing=True
                )
                assert (request.status, request.client_name) == (
                    "pending_review",
                    "内置机器人（企业微信）",
                )
                withdraw = db.exec(
                    select(ImAction).where(
                        ImAction.org_id == p.org_id, col(ImAction.consumed_at).is_(None)
                    )
                ).all()
                await pump(
                    action=next(a for a in withdraw if a.action["action"] == "withdraw")
                )
                db.refresh(request)
                assert request.status == "withdrawn"
                before = len(fake["requests"])
                await adapter.render("user", "长" * 3001, [("确认提交", "token")])
                assert "template_card" not in fake["requests"][before]["body"]
                await fake["socket"].send(
                    json.dumps(
                        {
                            "cmd": "aibot_event_callback",
                            "headers": {"req_id": "disconnect"},
                            "body": {"event": {"eventtype": "disconnected_event"}},
                        }
                    )
                )
                with pytest.raises(ImError, match="connection_conflict"):
                    async for _ in adapter.poll():
                        pass
                assert adapter.checked_at is None
                with pytest.raises(ImError, match="connection_conflict"):
                    await adapter.send_text("user", "接管后禁止重连或发送")
            finally:
                await adapter.close()
                assert all(t.done() for t in adapter.tasks)

    asyncio.run(run())


def test_connection_and_privacy(
    demo: tuple,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    p, setting = setup(db, demo)
    caplog.set_level(logging.DEBUG)

    async def run() -> None:
        async with server(monkeypatch) as fake:
            monkeypatch.setattr(wecom, "HEARTBEAT_INTERVAL", 0.01)
            probe = wecom.Wecom(p.org_id, CREDS)
            try:
                await probe.test_connection()
                before = probe.checked_at
                await asyncio.wait_for(fake["ping"].wait(), 1)
                await asyncio.sleep(0.01)
                assert probe.checked_at > before
            finally:
                await probe.close()
            fake["requests"].clear()
            assert not (await settings.test_connection(db, p.org_id, "wecom")).ok
            assert fake["requests"] == []
            setting.connection_checked_at = utc_now()
            db.commit()
            assert (await settings.test_connection(db, p.org_id, "wecom")).ok
            assert fake["requests"] == []
            setting.connection_checked_at -= timedelta(minutes=3)
            db.commit()
            assert not (await settings.test_connection(db, p.org_id, "wecom")).ok
            setting.enabled = False
            db.commit()
            with sentry_sdk.init(dsn="", default_integrations=True):

                def fail_release() -> None:
                    pytest.fail("Must not detect Sentry release during requests")

                monkeypatch.setattr(
                    "sentry_sdk.client.get_default_release", fail_release
                )
                assert (await settings.test_connection(db, p.org_id, "wecom")).ok
                fake["code"] = 40001
                result = await settings.test_connection(db, p.org_id, "wecom")
                assert not result.ok and result.message == "应用凭据无效或没有权限"
            assert all(
                secret not in caplog.text
                for secret in ("private-secret", "private-upstream-body")
            )

    asyncio.run(run())


@pytest.mark.parametrize(
    "case", ["match", "disabled", "duplicate", "foreign", "inactive", "conflict"]
)
def test_email_binding(demo: tuple, db: Session, case: str) -> None:
    p, setting = setup(db, demo)
    person = demo[1]["E02"]
    email = person.work_email.upper()
    if case == "disabled":
        setting.email_auto_bind = False
    elif case == "duplicate":
        demo[1]["E03"].work_email = person.work_email
        db.add(demo[1]["E03"])
    elif case == "foreign":
        email = "foreign-only@synthetic.example"
    elif case == "inactive":
        membership = db.exec(
            select(OrgMembership).where(
                OrgMembership.org_id == p.org_id,
                OrgMembership.employee_id == p.employee_id,
            )
        ).one()
        user = db.get(User, membership.user_id)
        user.is_active = False
        db.add(user)
    elif case == "conflict":
        assert binding.bind(
            db,
            p.org_id,
            "wecom",
            "other-user",
            "other-user",
            CREDS["bot_id"],
            binding.create_code(db, p).code,
        )
    db.add(setting)
    foreign = db.exec(
        select(Organization).where(Organization.name == "Synthetic foreign tenant")
    ).one()
    db.add(Employee(org_id=foreign.id, display_name="合成外企业员工", work_email=email))
    db.commit()
    try:
        assert binding.bind_email(
            db,
            p.org_id,
            "wecom",
            "user",
            "user",
            CREDS["bot_id"],
            setting.credentials,
            email,
        ) == (case == "match")
    finally:
        if case == "inactive":
            user.is_active = True
            db.add(user)
            db.commit()
    if case == "match":
        assert (
            db.exec(select(ImBinding).where(ImBinding.org_id == p.org_id))
            .one()
            .bound_via
            == "email"
        )


def test_directory_fallback_and_privacy(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)

    async def run() -> None:
        adapter = wecom.Wecom(uuid4(), CREDS)
        calls = []

        def respond(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            if request.url.path.endswith("gettoken"):
                return httpx.Response(200, json={"access_token": "private-token"})
            if request.url.path.endswith("openuserid_to_userid"):
                return httpx.Response(
                    200,
                    json={
                        "userid_list": [{"open_userid": "encrypted", "userid": "plain"}]
                    },
                )
            if request.url.params.get("userid") == "encrypted":
                return httpx.Response(200, json={"errcode": 60111})
            return httpx.Response(
                200, json={"errcode": 0, "biz_mail": "work@synthetic.example"}
            )

        await adapter.client.aclose()
        adapter.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        try:
            assert (
                await adapter.enterprise_email("encrypted") == "work@synthetic.example"
            )
            assert len(calls) == 4
            adapter.credentials = {"bot_id": "bot", "secret": "secret"}
            assert await adapter.enterprise_email("user") is None
        finally:
            await adapter.close()

    asyncio.run(run())
    assert "private-token" not in caplog.text and "private-contact" not in caplog.text


def test_migration(migration_engine: Engine) -> None:
    migrate(migration_engine, "downgrade", "b130001bc001")
    assert "connection_checked_at" not in {
        c["name"] for c in inspect(migration_engine).get_columns("im_setting")
    }
    migrate(migration_engine, "upgrade", "head")
    assert "connection_checked_at" in {
        c["name"] for c in inspect(migration_engine).get_columns("im_setting")
    }


def test_gateway_heartbeat_is_throttled(
    demo: tuple, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    p, setting = setup(db, demo)
    initial = utc_now()
    now = [initial]
    snapshots = []
    original_metadata = setting.updated_at, setting.updated_by_user_id
    monkeypatch.setattr(gateway, "utc_now", lambda: now[0])

    class Fake(wecom.Wecom):
        async def test_connection(self) -> str:
            self.checked_at = now[0]
            return CREDS["bot_id"]

        async def poll(self) -> AsyncIterator[Event]:
            row = db.get(ImSetting, (p.org_id, "wecom"), populate_existing=True)
            snapshots.append(row.connection_checked_at)
            assert (row.updated_at, row.updated_by_user_id) == original_metadata
            now[0] += timedelta(seconds=20)
            self.checked_at = now[0]
            if len(snapshots) == 4:
                raise ImError("connection_conflict")
            yield Event(p.org_id, "wecom", uuid4().hex)

    async def handle(
        self: gateway.Gateway,
        event: Event,
        identity: str,
        credentials: str,
        adapter: Adapter,
    ) -> None:
        pass

    monkeypatch.setitem(gateway.ADAPTERS, "wecom", Fake)
    monkeypatch.setattr(gateway.Gateway, "handle", handle)
    asyncio.run(gateway.Gateway(engine).connect(p.org_id, "wecom", setting.credentials))
    assert snapshots == [initial, initial, initial, initial + timedelta(seconds=60)]
    db.refresh(setting)
    assert setting.connection_checked_at is None
