"""Exercise the gateway with real PostgreSQL waits and synthetic adapters."""

import asyncio
import time
from collections.abc import AsyncIterator, Callable, Generator
from concurrent.futures import ThreadPoolExecutor
from threading import Event as ThreadEvent

import anyio
import pytest
from sqlalchemy import Engine
from sqlmodel import Session, create_engine

from app.domains.bot import schemas as s
from app.domains.im import actions, gateway
from app.worker import monitor
from app.worker.models import WorkerHeartbeat
from tests.domains.test_im_conversation import setup
from tests.domains.test_im_gateway import event


@pytest.fixture
def small_pool(db: Session, request: pytest.FixtureRequest) -> Generator[Engine]:
    isolated = create_engine(
        db.get_bind().url,
        pool_size=getattr(request, "param", 1),
        max_overflow=0,
        pool_timeout=1,
        connect_args={"options": "-c statement_timeout=1000"},
    )
    try:
        yield isolated
        assert isolated.pool.checkedout() == 0
    finally:
        isolated.dispose()


def test_handle_pool_wait_keeps_sends_running(
    demo: tuple, db: Session, small_pool: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    p, setting = setup(db, demo)
    incoming = event(p.org_id, "合成消息")
    conversation = actions.conversation(db, p, incoming, "12345")
    reply = actions.Reply(
        conversation.id, s.TurnResponse(messages=[s.TextMessage(text="合成回复")])
    )
    credentials = setting.credentials
    db.commit()
    g = gateway.Gateway(small_pool)
    monkeypatch.setattr(g, "dispatch", lambda *_: reply)

    async def run() -> None:
        limiter = anyio.to_thread.current_default_thread_limiter()
        limiter.total_tokens = 2
        started = asyncio.Event()
        sent: list[str] = []
        heartbeat = False

        class Adapter:
            async def render(self, _chat: str, body: str, _buttons: list) -> str:
                started.set()
                await asyncio.sleep(0.05)
                sent.append(body)
                return str(len(sent))

        async def tick() -> None:
            nonlocal heartbeat
            await asyncio.sleep(0.01)
            heartbeat = True

        # A small executor also exposes releases queued behind connection waiters.
        with ThreadPoolExecutor(max_workers=2) as executor:
            asyncio.get_running_loop().set_default_executor(executor)
            first = asyncio.create_task(
                g.handle(incoming, "12345", credentials, Adapter())
            )
            await asyncio.wait_for(started.wait(), 2)
            waiting = [
                asyncio.create_task(
                    g.handle(event(p.org_id, "另一条"), "12345", credentials, Adapter())
                )
                for _ in range(3)
            ]
            tick_task = asyncio.create_task(tick())
            try:
                await asyncio.wait_for(asyncio.gather(first, *waiting, tick_task), 3)
                assert heartbeat and len(sent) == 4
            finally:
                for task in (first, *waiting, tick_task):
                    task.cancel()
                await asyncio.gather(first, *waiting, tick_task, return_exceptions=True)

    asyncio.run(run())


@pytest.mark.parametrize(
    "path", ["refresh", "connect", "heartbeat", "worker", "notify", "email", "ack"]
)
def test_maintenance_pool_wait_does_not_block_loop(
    demo: tuple,
    db: Session,
    small_pool: Engine,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    path: str,
) -> None:
    from datetime import timedelta

    from app.domains.base import utc_now
    from app.domains.im import notifications
    from app.domains.im.adapters.base import ImError
    from app.domains.im.adapters.wecom import Wecom
    from app.domains.im.models import ImSetting

    p, setting = setup(db, demo)
    credentials = setting.credentials
    g = gateway.Gateway(small_pool)
    now = utc_now()
    db.add(
        ImSetting(
            org_id=p.org_id,
            platform="wecom",
            enabled=True,
            credentials=credentials,
            updated_by_user_id=p.user_id,
        )
    )
    db.commit()
    closed = False
    polls = 0
    worker_finished = ThreadEvent()
    worker_alerts = g.worker_alerts

    def alerts() -> None:
        worker_alerts()
        worker_finished.set()

    monkeypatch.setattr(g, "worker_alerts", alerts)

    class Adapter(Wecom):
        def __init__(self, *_: object) -> None:
            self.org_id = p.org_id
            self.credentials = {"bot_id": "synthetic"}
            self.checked_at = now

        async def test_connection(self) -> str:
            return "synthetic"

        async def poll(self) -> AsyncIterator[gateway.Event]:
            nonlocal polls
            polls += 1
            if polls == 1:
                if False:
                    yield event(p.org_id, "未使用")
                return
            raise ImError("invalid_credentials")

        async def close(self) -> None:
            nonlocal closed
            closed = True

    from app.domains.im.adapters.feishu import Feishu

    class AckAdapter:
        async def send_text(self, _chat: str, _text: str) -> str:
            return "synthetic"

    class EmailAdapter(Feishu):
        def __init__(self) -> None:
            pass

        async def enterprise_email(self, _user: str) -> str | None:
            return None

        async def send_text(self, _chat: str, _text: str) -> str:
            return "synthetic"

    def dispatch(
        _event: object, _identity: str, _credentials: str, on_started: object
    ) -> str:
        on_started()
        return "合成回复"

    async def no_refresh() -> None:
        return

    async def run() -> None:
        monkeypatch.setitem(gateway.ADAPTERS, "wecom", Adapter)
        if path in ("email", "ack"):
            monkeypatch.setattr(g, "dispatch", dispatch)
            incoming = event(p.org_id, "合成消息")
            if path == "email":
                from dataclasses import replace

                db.add(
                    ImSetting(
                        org_id=p.org_id,
                        platform="feishu",
                        enabled=True,
                        credentials=credentials,
                        bot_identity="12345",
                        updated_by_user_id=p.user_id,
                    )
                )
                db.commit()
                incoming = replace(incoming, platform="feishu")
            operation = g.handle(
                incoming,
                "12345",
                credentials,
                EmailAdapter() if path == "email" else AckAdapter(),
            )
        elif path == "heartbeat":
            times = iter([now - timedelta(minutes=2), now, now])
            monkeypatch.setattr(gateway, "utc_now", lambda: next(times))
            operation = g.poll(Adapter(), "synthetic", credentials)
        elif path == "connect":
            operation = g.connect(p.org_id, "wecom", credentials)
        elif path == "refresh":
            operation = g.refresh()
        elif path == "notify":
            operation = notifications.send_pending(small_pool, {})
        else:
            # Keep any heartbeat left by a previous test fresh during the database wait.
            heartbeat_row = db.get(WorkerHeartbeat, 1)
            monitor_now = heartbeat_row.beat_at if heartbeat_row is not None else now
            monkeypatch.setattr(gateway, "utc_now", lambda: monitor_now)
            monkeypatch.setattr(monitor, "PROCESS_STARTED_AT", monitor_now)
            monkeypatch.setattr(g, "refresh", no_refresh)
            operation = g.run()
        held = small_pool.connect()
        ticks: list[float] = []
        deadline = time.monotonic() + 0.55

        async def heartbeat() -> None:
            while time.monotonic() < deadline:
                await asyncio.sleep(0.05)
                if time.monotonic() < deadline:
                    ticks.append(time.monotonic())

        pulse = asyncio.create_task(heartbeat())
        task = asyncio.create_task(operation)
        try:
            await asyncio.sleep(0.05)
            # A synchronous checkout would return only after pool_timeout here.
            assert not task.done()
            await pulse
            assert len(ticks) >= 8, "database wait blocked the event-loop heartbeat"
            held.close()
            if path == "worker":
                assert await asyncio.to_thread(worker_finished.wait, 2)
                await asyncio.sleep(0.1)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            elif path == "heartbeat":
                with pytest.raises(ImError):
                    await asyncio.wait_for(task, 2)
            else:
                await asyncio.wait_for(task, 2)
            assert "pool_timeout" not in caplog.text
            assert "code=storage" not in caplog.text
        finally:
            held.close()
            pulse.cancel()
            task.cancel()
            await asyncio.gather(task, pulse, return_exceptions=True)
            for _, connection in g.tasks.values():
                connection.cancel()
            await asyncio.gather(
                *(t for _, t in g.tasks.values()), return_exceptions=True
            )
        if path == "connect":
            assert closed and not g.adapters and not g.identities

    asyncio.run(run())


@pytest.mark.parametrize("cancel", [False, True])
def test_connect_row_lock_and_old_credentials(
    demo: tuple,
    db: Session,
    small_pool: Engine,
    monkeypatch: pytest.MonkeyPatch,
    cancel: bool,
) -> None:
    from sqlmodel import select

    from app.domains.base import utc_now
    from app.domains.im.adapters.wecom import Wecom
    from app.domains.im.models import ImSetting

    p, setting = setup(db, demo)
    credentials = setting.credentials
    db.add(
        ImSetting(
            org_id=p.org_id,
            platform="wecom",
            enabled=True,
            credentials=credentials,
            bot_identity="old",
            updated_by_user_id=p.user_id,
        )
    )
    db.commit()
    closed = False
    now = utc_now()
    g = gateway.Gateway(small_pool)

    class Adapter(Wecom):
        def __init__(self, *_: object) -> None:
            self.org_id = p.org_id
            self.credentials = {"bot_id": "synthetic"}
            self.checked_at = now

        async def test_connection(self) -> str:
            return "synthetic"

        async def close(self) -> None:
            nonlocal closed
            closed = True

    async def run() -> None:
        monkeypatch.setitem(gateway.ADAPTERS, "wecom", Adapter)
        with Session(db.get_bind()) as locker:
            row = locker.exec(
                select(ImSetting)
                .where(ImSetting.org_id == p.org_id, ImSetting.platform == "wecom")
                .with_for_update()
            ).one()
            task = asyncio.create_task(g.connect(p.org_id, "wecom", credentials))
            try:
                await asyncio.sleep(0.05)
                assert not task.done()
                if cancel:
                    task.cancel()
                    await asyncio.sleep(0.01)
                    assert not task.done()
                row.credentials = "replacement"
                row.bot_identity = "new"
                row.connection_checked_at = now
                locker.add(row)
                locker.commit()
                if cancel:
                    with pytest.raises(asyncio.CancelledError):
                        await asyncio.wait_for(task, 2)
                else:
                    await asyncio.wait_for(task, 2)
            finally:
                locker.rollback()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        assert closed and not g.adapters and not g.identities

    asyncio.run(run())
    db.expire_all()
    current = db.get(ImSetting, (p.org_id, "wecom"))
    assert current and current.credentials == "replacement"
    assert current.bot_identity == "new" and current.connection_checked_at == now


@pytest.mark.parametrize("small_pool", [1, 2], indirect=True)
@pytest.mark.parametrize("cancel", [False, True])
def test_notification_claim_send_and_cancellation(
    demo: tuple, db: Session, small_pool: Engine, cancel: bool
) -> None:
    from sqlmodel import select

    from app.domains.im import notifications
    from app.domains.im.models import ImNotification
    from app.domains.inbox.models import InboxItem, InboxKind
    from app.domains.inbox.service import add_item

    p, setting = setup(db, demo)
    credentials = setting.credentials
    add_item(
        db,
        InboxItem(
            org_id=p.org_id,
            recipient_employee_id=p.employee_id,
            kind=InboxKind.QUESTION,
            message="合成通知",
        ),
    )
    db.commit()
    notification_id = db.exec(
        select(ImNotification.id).where(ImNotification.org_id == p.org_id)
    ).one()

    async def run() -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        sent = []

        class Adapter:
            async def send_text(self, _chat: str, body: str) -> None:
                started.set()
                await release.wait()
                sent.append(body)

        adapters = {(p.org_id, "telegram"): (credentials, Adapter())}
        first = asyncio.create_task(notifications.send_pending(small_pool, adapters))
        await asyncio.wait_for(started.wait(), 2)
        second = asyncio.create_task(notifications.send_pending(small_pool, adapters))
        try:
            await asyncio.sleep(0.05)
            assert not first.done()
            if small_pool.pool.size() == 2:
                # Another sender skips the row locked by the first transaction.
                await asyncio.wait_for(second, 2)
            else:
                assert not second.done()
            if cancel:
                first.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await first
            release.set()
            if cancel:
                await notifications.send_pending(small_pool, adapters)
            await asyncio.wait_for(
                asyncio.gather(second, *([] if cancel else [first])), 2
            )
            assert len(sent) == 1
        finally:
            release.set()
            first.cancel()
            second.cancel()
            await asyncio.gather(first, second, return_exceptions=True)

    asyncio.run(run())
    db.expire_all()
    row = db.get(ImNotification, notification_id)
    assert row and row.status == "sent" and row.attempts == 1


@pytest.mark.parametrize("cancel", [False, True])
def test_handle_wait_rechecks_configuration_and_joins_cancelled_dispatch(
    demo: tuple,
    db: Session,
    small_pool: Engine,
    monkeypatch: pytest.MonkeyPatch,
    cancel: bool,
) -> None:
    from threading import Event as ThreadEvent

    from app.domains.im.models import ImSetting

    p, setting = setup(db, demo)
    credentials = setting.credentials
    incoming = event(p.org_id, "合成消息")
    g = gateway.Gateway(small_pool)
    entered, release, completed = ThreadEvent(), ThreadEvent(), ThreadEvent()

    def dispatch(
        _event: object,
        _identity: str,
        _credentials: str,
        on_started: Callable[[], None],
    ) -> str:
        # This connection and transaction must finish before the cancelled handle returns.
        with Session(small_pool) as session:
            assert session.get(ImSetting, (p.org_id, "telegram"))
            entered.set()
            assert release.wait(2)
        on_started()
        completed.set()
        return "合成回复"

    monkeypatch.setattr(g, "dispatch", dispatch)

    async def run() -> None:
        sent: list[str] = []

        class Adapter:
            async def send_text(self, _chat: str, body: str) -> None:
                sent.append(body)

        task = asyncio.create_task(g.handle(incoming, "12345", credentials, Adapter()))
        try:
            async with asyncio.timeout(2):
                while not entered.is_set():
                    await asyncio.sleep(0.001)
            if cancel:
                task.cancel()
                await asyncio.sleep(0.02)
                assert not task.done()
            else:
                setting.enabled = False
                setting.credentials = "replacement"
                db.add(setting)
                db.commit()
            release.set()
            if cancel:
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                await task
            assert completed.is_set() and sent == []
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
