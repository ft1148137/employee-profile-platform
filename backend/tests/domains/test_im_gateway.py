import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, inspect, text
from sqlmodel import Session, select

from app.authz.deps import get_org_principal
from app.authz.principal import Principal
from app.core.config import settings
from app.core.db import engine
from app.core.secrets import encrypt_secret
from app.domains.account.limiter import LoginLimited
from app.domains.account.models import LoginAttempt
from app.domains.base import utc_now
from app.domains.im import binding, gateway
from app.domains.im import settings as im_settings
from app.domains.im.adapters import telegram
from app.domains.im.adapters.base import Event, ImError
from app.domains.im.adapters.telegram import Telegram
from app.domains.im.models import ImBindCode, ImBinding, ImEvent, ImSetting
from app.domains.im.schemas import ImSettingUpdate
from app.domains.org.models import Organization, OrgMembership
from app.models import User
from tests.utils.migrations import migrate

TOKEN = "12345:synthetic-token"


def principal(db: Session, org_id: UUID, employee_id: UUID) -> Principal:
    membership = db.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == org_id, OrgMembership.employee_id == employee_id
        )
    ).one()
    user = db.get(User, membership.user_id)
    assert user
    return get_org_principal(org_id, user, db)


def enable(db: Session, p: Principal) -> ImSetting:
    assert p.user_id
    row = ImSetting(
        org_id=p.org_id,
        platform="telegram",
        enabled=True,
        credentials=encrypt_secret(json.dumps({"bot_token": TOKEN})),
        bot_identity="12345",
        updated_by_user_id=p.user_id,
    )
    db.add(row)
    db.commit()
    return row


def event(
    org_id: UUID, message: str, *, user: str = "100", private: bool = True
) -> Event:
    return Event(org_id, "telegram", uuid4().hex, user, user, private, message)


@pytest.mark.parametrize("kind", ["document", "photo"])
def test_telegram_attachment_notice(
    demo: tuple, db: Session, telegram_server: dict, kind: str
) -> None:
    p = principal(db, demo[1]["E02"].org_id, demo[1]["E02"].id)
    setting = enable(db, p)
    telegram_server["updates"] = [
        {
            "update_id": 1,
            "message": {
                "from": {"id": 100, "is_bot": False},
                "chat": {"id": 100, "type": "private"},
                kind: [{"file_id": "synthetic"}]
                if kind == "photo"
                else {"file_id": "synthetic"},
            },
        }
    ]

    async def run() -> None:
        adapter = Telegram(p.org_id, {"bot_token": TOKEN})
        try:
            async for incoming in adapter.poll():
                await gateway.Gateway(engine).handle(
                    incoming, "12345", setting.credentials, adapter
                )
        finally:
            await adapter.close()

    asyncio.run(run())
    assert (
        telegram_server["requests"][-1][1]["text"] == "该入口暂不支持文件，请在网页上传"
    )


def test_http_binding_cycle(
    demo: tuple,
    db: Session,
    client: TestClient,
    telegram_server: dict,
    caplog: pytest.LogCaptureFixture,
) -> None:
    base, people, auth = demo
    p = principal(db, people["E02"].org_id, people["E02"].id)
    setting = enable(db, p)
    g = gateway.Gateway(engine)
    response = client.post(base + "/me/im-bind-code", headers=auth["employee"])
    assert (
        response.status_code == 200 and response.headers["cache-control"] == "no-store"
    )
    code = response.json()["code"]
    assert len(code) == 6 and code.isdigit()
    stored = db.exec(select(ImBindCode).where(ImBindCode.org_id == p.org_id)).one()
    assert stored.code_hash != code and code not in repr(stored)
    caplog.set_level(logging.DEBUG)

    async def run() -> None:
        adapter = Telegram(p.org_id, {"bot_token": TOKEN})
        try:
            assert await adapter.test_connection() == "12345"

            async def pump(update_id: int, message: str) -> None:
                telegram_server["updates"] = [
                    {
                        "update_id": update_id,
                        "message": {
                            "from": {"id": 100, "is_bot": False},
                            "chat": {"id": 100, "type": "private"},
                            "text": message,
                        },
                    }
                ]
                async for incoming in adapter.poll():
                    await g.handle(incoming, "12345", setting.credentials, adapter)

            await pump(10, "绑定 " + code)
            await pump(10, "绑定 " + code)
            sends = [
                body
                for method, body in telegram_server["requests"]
                if method == "sendMessage"
            ]
            assert len(sends) == 1 and sends[0]["text"].startswith("绑定成功")
            rows = client.get(base + "/me/im-bindings", headers=auth["employee"]).json()
            assert (
                len(rows) == 1
                and rows[0]["active"]
                and rows[0]["external_user_id"] == "100"
            )
            assert TOKEN not in json.dumps(rows)
            await pump(11, "合成私聊正文")
            assert "先配置模型" in telegram_server["requests"][-1][1]["text"]
            assert (
                client.delete(
                    base + "/me/im-bindings/telegram", headers=auth["employee"]
                ).status_code
                == 204
            )
            await pump(12, "解绑后的合成私聊")
            assert telegram_server["requests"][-1][1]["text"] == binding.GUIDE
            assert adapter.offset == 13
            assert (
                client.get(base + "/me/im-bindings", headers=auth["employee"]).json()
                == []
            )
        finally:
            await adapter.close()

    asyncio.run(run())
    assert TOKEN not in caplog.text and "合成私聊正文" not in caplog.text
    assert "解绑后的合成私聊" not in caplog.text


def test_binding_limit_is_shared_across_accounts(
    demo: tuple, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = principal(db, demo[1]["E02"].org_id, demo[1]["E02"].id)
    enable(db, p)
    code = binding.create_code(db, p).code
    # A small shared threshold exercises the real limiter with distinct accounts.
    monkeypatch.setattr(settings, "LOGIN_MAX_FAILURES_PER_IP", 6)
    wrong = "000000" if code != "000000" else "000001"
    for i in range(settings.LOGIN_MAX_FAILURES_PER_IP):
        user = str(100 + i % 3)
        assert not binding.bind(db, p.org_id, "telegram", user, user, "12345", wrong)
    attempts = db.exec(
        select(LoginAttempt).where(LoginAttempt.ip == f"im:{p.org_id}:telegram")
    ).all()
    assert len(attempts) == settings.LOGIN_MAX_FAILURES_PER_IP
    assert len({a.identifier_norm for a in attempts}) == 3
    with pytest.raises(LoginLimited):
        binding.bind(db, p.org_id, "telegram", "999", "999", "12345", code)
    db.rollback()
    assert binding.list_bindings(db, p) == []
    stored = db.exec(select(ImBindCode).where(ImBindCode.org_id == p.org_id)).one()
    assert stored.used_at is None


def test_gateway_polls_and_sends_through_http(
    demo: tuple, db: Session, telegram_server: dict
) -> None:
    _, people, _ = demo
    p = principal(db, people["E02"].org_id, people["E02"].id)
    setting = enable(db, p)
    code = binding.create_code(db, p).code
    telegram_server["updates"] = [
        {
            "update_id": 1001,
            "message": {
                "from": {"id": 100, "is_bot": False},
                "chat": {"id": 100, "type": "private"},
                "text": "绑定 " + code,
            },
        }
    ]

    async def run() -> None:
        g = gateway.Gateway(engine)
        task = asyncio.create_task(g.connect(p.org_id, "telegram", setting.credentials))
        try:

            async def received() -> None:
                while not any(
                    method == "sendMessage" for method, _ in telegram_server["requests"]
                ):
                    await asyncio.sleep(0.01)

            await asyncio.wait_for(received(), timeout=5)
            methods = [method for method, _ in telegram_server["requests"]]
            assert methods[:3] == ["getMe", "getWebhookInfo", "getUpdates"]
            assert binding.current_binding(
                db, p.org_id, "telegram", "100", "100", "12345"
            )
            # One blocked recipient must not stop the entire enterprise's polling.
            telegram_server["responses"]["sendMessage"] = (
                403,
                b"private-upstream-body",
            )
            telegram_server["updates"] = [
                {
                    "update_id": 1002,
                    "message": {
                        "from": {"id": 200, "is_bot": False},
                        "chat": {"id": 200, "type": "private"},
                        "text": "hello",
                    },
                }
            ]

            async def consumed() -> None:
                while not any(
                    method == "getUpdates" and body["offset"] == 1003
                    for method, body in telegram_server["requests"]
                ):
                    await asyncio.sleep(0.01)

            await asyncio.wait_for(consumed(), timeout=5)
            assert not task.done()
            # Replacing the bot invalidates the old binding in personal settings too.
            db.refresh(setting)
            setting.bot_identity = "99999"
            db.add(setting)
            db.commit()
            assert not binding.list_bindings(db, p)[0].active
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            assert not g.identities

    asyncio.run(run())


def test_codes_scope_reuse_and_rate_limit(demo: tuple, db: Session) -> None:
    _, people, _ = demo
    p = principal(db, people["E02"].org_id, people["E02"].id)
    other = principal(db, p.org_id, people["E01"].id)
    setting = enable(db, p)
    g = gateway.Gateway(engine)
    code = binding.create_code(db, p).code
    assert (
        g.dispatch(
            event(p.org_id, "绑定 " + code, private=False), "12345", setting.credentials
        )
        == "请私聊我"
    )
    assert (
        g.dispatch(event(p.org_id, "hello"), "12345", setting.credentials)
        == binding.GUIDE
    )
    regenerated = binding.create_code(db, p).code
    assert (
        g.dispatch(event(p.org_id, "绑定 " + code), "12345", setting.credentials)
        == binding.GUIDE
    )
    assert g.dispatch(
        event(p.org_id, "绑定 " + regenerated), "12345", setting.credentials
    ).startswith("绑定成功")
    assert (
        g.dispatch(
            event(p.org_id, "绑定 " + regenerated, user="200"),
            "12345",
            setting.credentials,
        )
        == binding.GUIDE
    )
    # A valid second employee's code cannot take over an already bound account.
    second = binding.create_code(db, other).code
    assert (
        g.dispatch(event(p.org_id, "绑定 " + second), "12345", setting.credentials)
        == binding.GUIDE
    )
    assert (
        len(db.exec(select(ImBinding).where(ImBinding.org_id == p.org_id)).all()) == 1
    )
    assert binding.bind(db, p.org_id, "telegram", "200", "200", "12345", second)
    binding.unbind(db, p, "telegram")
    expired = binding.create_code(db, p).code
    row = db.exec(
        select(ImBindCode).where(
            ImBindCode.org_id == p.org_id, ImBindCode.employee_id == p.employee_id
        )
    ).one()
    row.expires_at = utc_now() - timedelta(seconds=1)
    db.add(row)
    db.commit()
    assert (
        g.dispatch(
            event(p.org_id, "绑定 " + expired, user="300"), "12345", setting.credentials
        )
        == binding.GUIDE
    )
    valid = binding.create_code(db, p).code
    for _ in range(5):
        assert (
            g.dispatch(
                event(p.org_id, "绑定 不是验证码", user="400"),
                "12345",
                setting.credentials,
            )
            == binding.GUIDE
        )
    assert (
        g.dispatch(
            event(p.org_id, "绑定 " + valid, user="400"), "12345", setting.credentials
        )
        == binding.GUIDE
    )
    assert not db.exec(
        select(ImBinding).where(
            ImBinding.org_id == p.org_id, ImBinding.external_user_id == "400"
        )
    ).all()
    foreign = Organization(slug=uuid4().hex, name="Synthetic tenant")
    db.add(foreign)
    db.commit()
    try:
        foreign_setting = ImSetting(
            org_id=foreign.id,
            platform="telegram",
            enabled=True,
            credentials=setting.credentials,
            bot_identity="54321",
            updated_by_user_id=p.user_id,
        )
        # A tenant needs its own membership for the settings composite foreign key.
        db.add(OrgMembership(org_id=foreign.id, user_id=p.user_id))
        db.flush()
        db.add(foreign_setting)
        db.commit()
        assert (
            g.dispatch(
                event(foreign.id, "绑定 " + valid), "54321", foreign_setting.credentials
            )
            == binding.GUIDE
        )
    finally:
        from tests.utils.org import delete_test_organization

        delete_test_organization(db, foreign.id)


def test_fresh_code_can_renew_the_same_binding(demo: tuple, db: Session) -> None:
    _, people, _ = demo
    p = principal(db, people["E02"].org_id, people["E02"].id)
    enable(db, p)
    assert binding.bind(
        db, p.org_id, "telegram", "100", "100", "12345", binding.create_code(db, p).code
    )
    user = db.get(User, p.user_id)
    previous = user.password_changed_at
    try:
        user.password_changed_at = utc_now()
        db.add(user)
        db.commit()
        assert (
            binding.current_binding(db, p.org_id, "telegram", "100", "100", "12345")
            is None
        )
        fresh = binding.create_code(db, p).code
        assert binding.bind(db, p.org_id, "telegram", "100", "100", "12345", fresh)
        assert binding.current_binding(db, p.org_id, "telegram", "100", "100", "12345")
        assert not binding.bind(db, p.org_id, "telegram", "100", "100", "12345", fresh)
    finally:
        user.password_changed_at = previous
        db.add(user)
        db.commit()


@pytest.mark.parametrize(
    "change", ["inactive", "must_change", "password", "membership", "bot", "chat"]
)
def test_binding_revalidates_identity(demo: tuple, db: Session, change: str) -> None:
    _, people, _ = demo
    p = principal(db, people["E02"].org_id, people["E02"].id)
    setting = enable(db, p)
    assert binding.bind(
        db, p.org_id, "telegram", "100", "100", "12345", binding.create_code(db, p).code
    )
    assert binding.current_binding(db, p.org_id, "telegram", "100", "100", "12345")
    user = db.get(User, p.user_id)
    member = db.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == p.org_id, OrgMembership.user_id == p.user_id
        )
    ).one()
    old = (
        user.is_active,
        user.must_change_password,
        user.password_changed_at,
        member.employee_id,
    )
    try:
        if change == "inactive":
            user.is_active = False
        elif change == "must_change":
            user.must_change_password = True
        elif change == "password":
            user.password_changed_at = utc_now()
        elif change == "membership":
            member.employee_id = None
        db.add_all([user, member])
        db.commit()
        identity = "99999" if change == "bot" else "12345"
        incoming = event(p.org_id, "合成的本人经历")
        if change == "chat":
            incoming = Event(
                p.org_id, "telegram", uuid4().hex, "100", "999", True, "合成正文"
            )
        assert (
            gateway.Gateway(engine).dispatch(incoming, identity, setting.credentials)
            == binding.GUIDE
        )
        if change not in ("bot", "chat"):
            assert not binding.list_bindings(db, p)[0].active
    finally:
        (
            user.is_active,
            user.must_change_password,
            user.password_changed_at,
            member.employee_id,
        ) = old
        db.add_all([user, member])
        db.commit()


def test_api_tenant_and_owner_permissions(
    demo: tuple, db: Session, client: TestClient
) -> None:
    base, people, auth = demo
    p = principal(db, people["E02"].org_id, people["E02"].id)
    enable(db, p)
    assert binding.bind(
        db, p.org_id, "telegram", "100", "100", "12345", binding.create_code(db, p).code
    )
    assert (
        client.post(base + "/me/im-bind-code", headers=auth["admin"]).status_code == 404
    )
    for method, suffix in [
        ("GET", "/im-bindings"),
        ("POST", "/im-bind-code"),
        ("DELETE", "/im-bindings/telegram"),
    ]:
        assert (
            client.request(
                method, base + "/me" + suffix, headers=auth["foreign"]
            ).status_code
            == 404
        )
    assert client.get(base + "/me/im-bindings", headers=auth["manager"]).json() == []
    assert (
        client.delete(
            base + "/me/im-bindings/telegram", headers=auth["manager"]
        ).status_code
        == 204
    )
    assert (
        len(client.get(base + "/me/im-bindings", headers=auth["employee"]).json()) == 1
    )


def test_migration_and_event_cleanup(
    migration_engine: Engine, demo: tuple, db: Session
) -> None:
    migrate(migration_engine, "downgrade", "b117002bc001")
    with migration_engine.connect() as connection:
        assert "im_binding" not in inspect(connection).get_table_names()
    migrate(migration_engine, "upgrade", "head")
    with migration_engine.connect() as connection:
        schema = inspect(connection)
        for name in ("im_binding", "im_bind_code"):
            assert any(
                fk["constrained_columns"] == ["org_id", "employee_id"]
                for fk in schema.get_foreign_keys(name)
            )
        assert schema.get_pk_constraint("im_event")["constrained_columns"] == [
            "org_id",
            "platform",
            "event_id",
        ]
        assert (
            connection.execute(
                text("SELECT count(*) FROM alembic_version")
            ).scalar_one()
            == 1
        )
    org_id = demo[1]["E02"].org_id
    db.add_all(
        [
            ImEvent(
                org_id=org_id,
                platform="telegram",
                event_id="old",
                received_at=utc_now() - timedelta(days=8),
            ),
            ImEvent(org_id=org_id, platform="telegram", event_id="recent"),
        ]
    )
    db.commit()
    binding.cleanup_events(db)
    assert [
        row.event_id
        for row in db.exec(select(ImEvent).where(ImEvent.org_id == org_id)).all()
    ] == ["recent"]


def test_telegram_webhook_chunks_rate_limit_and_normalization(
    telegram_server: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        adapter = Telegram(uuid4(), {"bot_token": TOKEN})
        try:
            telegram_server["webhook"] = "https://synthetic.example/hook"
            with pytest.raises(ImError, match="webhook_exists"):
                _ = [incoming async for incoming in adapter.poll()]
            assert not any(
                method == "getUpdates" for method, _ in telegram_server["requests"]
            )
            telegram_server["webhook"] = ""
            telegram_server["updates"] = [
                {
                    "update_id": 1,
                    "message": {
                        "from": {"id": 100, "is_bot": False},
                        "chat": {"id": -200, "type": "group"},
                        "text": "群消息",
                    },
                },
                {
                    "update_id": 2,
                    "message": {
                        "from": {"id": 100, "is_bot": False},
                        "chat": {"id": 300, "type": "private"},
                        "text": "不匹配的私聊",
                    },
                },
                {"update_id": 3},
            ]
            items = [incoming async for incoming in adapter.poll()]
            assert not items[0].private and items[0].external_chat_id == "-200"
            assert items[1].text is None and items[2].text is None
            assert adapter.offset == 4
            waits = []

            async def limited_sleep(seconds: float) -> None:
                waits.append(seconds)
                telegram_server["responses"].clear()

            monkeypatch.setattr(telegram.asyncio, "sleep", limited_sleep)
            telegram_server["responses"]["sendMessage"] = (
                429,
                b'{"parameters":{"retry_after":2}}',
            )
            await adapter.send_text("100", "合" * 6001)
            sends = [
                body["text"]
                for method, body in telegram_server["requests"]
                if method == "sendMessage"
            ]
            assert [len(message) for message in sends] == [3000, 3000, 3000, 1]
            assert waits == [2]
        finally:
            await adapter.close()
        with pytest.raises(ImError, match="network"):
            await adapter.send_text("100", "断线后的合成提醒")

    asyncio.run(run())


def test_connection_deadline_includes_platform_retry_delay(
    demo: tuple, db: Session, telegram_server: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = principal(db, demo[1]["E02"].org_id, demo[1]["E02"].id)
    enable(db, p)
    original = asyncio.timeout
    deadlines = []

    def short_deadline(seconds: float) -> asyncio.Timeout:
        deadlines.append(seconds)
        return original(0.01)

    monkeypatch.setattr(telegram.asyncio, "timeout", short_deadline)
    telegram_server["responses"]["getMe"] = (429, b'{"parameters":{"retry_after":60}}')

    result = asyncio.run(im_settings.test_connection(db, p.org_id, "telegram"))
    assert not result.ok and result.message == "连接超时，请检查网络"
    assert deadlines == [10]


def test_supervisor_changes_and_duplicate_bot(
    demo: tuple,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _, people, _ = demo
    p = principal(db, people["E02"].org_id, people["E02"].id)
    setting = enable(db, p)
    instances = []

    class Fake:
        def __init__(self, org_id: UUID, credentials: dict[str, str]) -> None:
            self.org_id, self.credentials, self.closed = org_id, credentials, False
            instances.append(self)

        async def test_connection(self) -> str:
            return "12345"

        async def poll(self) -> AsyncIterator[Event]:
            await asyncio.Event().wait()
            yield event(self.org_id, "unused")

        async def send_text(self, external_chat_id: str, text: str) -> None:
            raise AssertionError("No message expected")

        async def close(self) -> None:
            self.closed = True

    monkeypatch.setitem(gateway.ADAPTERS, "telegram", Fake)

    async def run() -> None:
        g = gateway.Gateway(engine)
        try:
            await g.refresh()
            await asyncio.sleep(0)
            assert len(instances) == 1
            original = g.tasks[p.org_id, "telegram"][1]
            # Re-saving the same secret and unrelated settings must not restart polling.
            before = setting.credentials
            im_settings.save_setting(
                db,
                p,
                "telegram",
                ImSettingUpdate(
                    enabled=True,
                    data_transfer_acknowledged=True,
                    credentials={"bot_token": TOKEN},
                ),
            )
            db.refresh(setting)
            assert before == setting.credentials
            await g.refresh()
            assert g.tasks[p.org_id, "telegram"][1] is original
            setting.credentials = encrypt_secret(
                json.dumps({"bot_token": "12345:rotated"})
            )
            db.add(setting)
            db.commit()
            await g.refresh()
            await asyncio.sleep(0)
            assert instances[0].closed and len(instances) == 2
            setting.enabled = False
            db.add(setting)
            db.commit()
            assert (
                g.dispatch(event(p.org_id, "private"), "12345", setting.credentials)
                is None
            )
            await g.refresh()
            assert instances[1].closed and not g.tasks
            setting.enabled = True
            db.add(setting)
            db.commit()
            await g.refresh()
            await asyncio.sleep(0)
            # Refuse a second tenant before it receives any updates from the same bot.
            foreign = Organization(
                slug=uuid4().hex, name="Synthetic duplicate bot tenant"
            )
            db.add(foreign)
            db.flush()
            db.add(OrgMembership(org_id=foreign.id, user_id=p.user_id))
            db.flush()
            db.add(
                ImSetting(
                    org_id=foreign.id,
                    platform="telegram",
                    enabled=True,
                    credentials=setting.credentials,
                    updated_by_user_id=p.user_id,
                )
            )
            db.commit()
            try:
                await g.refresh()
                await asyncio.sleep(0)
                assert g.tasks[foreign.id, "telegram"][1].done()
                assert "bot_already_used" in caplog.text
                assert instances[-1].closed
                count = len(instances)
                await g.refresh()
                assert len(instances) == count
            finally:
                from tests.utils.org import delete_test_organization

                delete_test_organization(db, foreign.id)
        finally:
            for _, task in g.tasks.values():
                task.cancel()
            await asyncio.gather(
                *(task for _, task in g.tasks.values()), return_exceptions=True
            )
            assert not g.identities and all(instance.closed for instance in instances)

    asyncio.run(run())


def test_network_backoff_and_shutdown(
    demo: tuple, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, people, _ = demo
    p = principal(db, people["E02"].org_id, people["E02"].id)
    setting = enable(db, p)
    delays = []
    closed = []

    class Offline:
        def __init__(self, _org: UUID, _credentials: dict[str, str]) -> None:
            pass

        async def test_connection(self) -> str:
            raise ImError("network", retryable=True)

        async def poll(self) -> AsyncIterator[Event]:
            raise AssertionError("Offline")
            yield event(p.org_id, "unused")

        async def send_text(self, external_chat_id: str, text: str) -> None:
            raise AssertionError("Offline")

        async def close(self) -> None:
            closed.append(True)

    async def backoff(seconds: float) -> None:
        delays.append(seconds)
        if len(delays) == 8:
            raise asyncio.CancelledError()

    monkeypatch.setitem(gateway.ADAPTERS, "telegram", Offline)
    monkeypatch.setattr(gateway.asyncio, "sleep", backoff)
    g = gateway.Gateway(engine)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(g.connect(p.org_id, "telegram", setting.credentials))
    assert delays == [1, 2, 4, 8, 16, 32, 60, 60] and len(closed) == 8
    delays.clear()

    async def stop(seconds: float) -> None:
        assert seconds == 10
        raise asyncio.CancelledError()

    monkeypatch.setattr(gateway.asyncio, "sleep", stop)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(g.run())
    assert all(task.done() for _, task in g.tasks.values())


def test_poll_handles_other_people_while_preserving_sender_order(
    demo: tuple, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = principal(db, demo[1]["E02"].org_id, demo[1]["E02"].id)
    setting = enable(db, p)

    async def run() -> None:
        first_started, other_replied = asyncio.Event(), asyncio.Event()
        order = []
        active = 0
        peak = 0

        class Fake:
            def __init__(self, *_args: object) -> None:
                self.closed = False

            async def test_connection(self) -> str:
                return "12345"

            async def poll(self) -> AsyncIterator[Event]:
                yield replace(event(p.org_id, "first"), external_user_id="A")
                yield replace(
                    event(p.org_id, "button"),
                    external_user_id="A",
                    callback_id="button",
                )
                for i in range(8):
                    yield replace(event(p.org_id, str(i)), external_user_id=str(i))
                await asyncio.Event().wait()

            async def close(self) -> None:
                assert active == 0
                self.closed = True

        async def handle(incoming: Event, *_args: object) -> None:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                order.append(incoming.text)
                if incoming.text == "first":
                    first_started.set()
                    await asyncio.wait_for(other_replied.wait(), 1)
                elif incoming.callback_id:
                    assert other_replied.is_set()
                else:
                    await first_started.wait()
                    other_replied.set()
                    await asyncio.sleep(0.01)
            finally:
                active -= 1

        g = gateway.Gateway(engine)
        monkeypatch.setitem(gateway.ADAPTERS, "telegram", Fake)
        monkeypatch.setattr(g, "handle", handle)
        connection = asyncio.create_task(
            g.connect(p.org_id, "telegram", setting.credentials)
        )
        try:
            await asyncio.wait_for(other_replied.wait(), 2)
            while len(order) < 10:
                await asyncio.sleep(0)
            assert order.index("first") < order.index("button")
            assert 2 <= peak <= 4
        finally:
            connection.cancel()
            await asyncio.gather(connection, return_exceptions=True)
            assert active == 0

    asyncio.run(run())


@pytest.mark.parametrize("code", ["recipient_unavailable", "timeout"])
def test_concurrent_poll_errors_and_cancellation(
    demo: tuple, db: Session, monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    p = principal(db, demo[1]["E02"].org_id, demo[1]["E02"].id)
    setting = enable(db, p)

    async def run() -> None:
        instances = []
        idle_started, idle_stopped, reconnected = (asyncio.Event() for _ in range(3))

        class Fake:
            def __init__(self, *_args: object) -> None:
                self.number = len(instances)
                instances.append(self)

            async def test_connection(self) -> str:
                if self.number:
                    reconnected.set()
                return "12345"

            async def poll(self) -> AsyncIterator[Event]:
                if self.number == 0:
                    yield replace(event(p.org_id, "idle"), external_user_id="A")
                    yield replace(event(p.org_id, "error"), external_user_id="B")
                    yield replace(event(p.org_id, "after"), external_user_id="C")
                await asyncio.Event().wait()

            async def close(self) -> None:
                assert idle_stopped.is_set()

        async def handle(incoming: Event, *_args: object) -> None:
            if incoming.text == "idle":
                idle_started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    idle_stopped.set()
            elif incoming.text == "error":
                await idle_started.wait()
                raise ImError(code, retryable=True)
            else:
                if code == "recipient_unavailable":
                    reconnected.set()

        async def backoff(_seconds: float) -> None:
            assert idle_stopped.is_set()

        monkeypatch.setitem(gateway.ADAPTERS, "telegram", Fake)
        monkeypatch.setattr(gateway.asyncio, "sleep", backoff)
        g = gateway.Gateway(engine)
        monkeypatch.setattr(g, "handle", handle)
        connection = asyncio.create_task(
            g.connect(p.org_id, "telegram", setting.credentials)
        )
        try:
            await asyncio.wait_for(reconnected.wait(), 2)
            assert len(instances) == (1 if code == "recipient_unavailable" else 2)
            assert not connection.done()
        finally:
            connection.cancel()
            await asyncio.gather(connection, return_exceptions=True)
            assert idle_stopped.is_set()

    asyncio.run(run())
