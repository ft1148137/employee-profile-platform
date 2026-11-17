"""Real web/IM sends must keep progressing while delivery waits on PostgreSQL."""

import asyncio
import json
from collections.abc import Awaitable, Callable, Generator
from threading import Event as ThreadEvent

import anyio
import pytest
from fastapi import Request
from sqlalchemy import Engine, event
from sqlmodel import Session, create_engine, select

from app.domains.bot import schemas as s
from app.domains.bot import store, streaming
from app.domains.im import actions
from app.domains.im.adapters.base import Event
from app.domains.im.models import ImAction
from app.domains.model.pi_client import TurnStream
from app.domains.org.models import Organization
from tests.domains.test_im_gateway import principal


@pytest.fixture
def small_pool(db: Session) -> Generator[Engine]:
    engine = create_engine(
        db.get_bind().url,
        pool_size=2,
        max_overflow=0,
        pool_timeout=1,
        connect_args={"options": "-c statement_timeout=1000"},
    )
    try:
        yield engine
        assert engine.pool.checkedout() == 0
    finally:
        engine.dispose()


@pytest.fixture
def send_reply(
    db: Session, demo: tuple, small_pool: Engine, monkeypatch: pytest.MonkeyPatch
) -> Callable[[str, Callable[[], Awaitable[None]]], Awaitable[None]]:
    p = principal(db, demo[1]["E02"].org_id, demo[1]["E02"].id)
    session = store.create_session(db, p, channel="feishu")
    response = s.TurnResponse(
        messages=[
            s.CapacityCard(
                kind="demand_reply",
                title="合成卡片",
                content="合成回复",
                fields=[],
                arguments={},
                actions=[
                    s.CapacityAction(action="confirm_capacity", card_id=session.id)
                ],
            )
        ]
    )
    response._principal = p
    response._scope = store.session_scope(db, p)
    db.rollback()
    monkeypatch.setattr(streaming, "engine", small_pool)
    monkeypatch.setattr(streaming, "cancel_run", lambda _: None)

    async def send(channel: str, network: Callable[[], Awaitable[None]]) -> None:
        if channel == "web":

            def run(_db: Session, _stream: TurnStream) -> s.TurnResponse:
                return response

            async def asgi_send(message: dict) -> None:
                if message.get("body"):
                    data = json.loads(message["body"])
                    assert data["type"] == "final"
                    assert data["response"]["messages"][0]["title"] == "合成卡片"
                    await network()

            await streaming.response(run, Request({"type": "http"})).stream_response(
                asgi_send
            )
        else:

            class Adapter:
                async def render(self, _chat: str, _body: str, buttons: list) -> str:
                    assert len(buttons) == 1
                    await network()
                    return "synthetic-message"

            incoming = Event(p.org_id, "feishu", "event", "user", "chat", True)
            with Session(small_pool) as sending:
                await actions.deliver(
                    sending,
                    p,
                    incoming,
                    "synthetic-app",
                    actions.Reply(session.id, response),
                    Adapter(),
                )

    return send


@pytest.mark.parametrize("channel", ["web", "feishu"])
def test_pool_wait_does_not_stop_prior_sends(
    send_reply: Callable, small_pool: Engine, channel: str
) -> None:
    async def exercise() -> None:
        # Exit must remain possible even when every entry worker is waiting on DB.
        anyio.to_thread.current_default_thread_limiter().total_tokens = 1
        entered = asyncio.Queue()
        release = asyncio.Event()
        completed = []

        async def network() -> None:
            entered.put_nowait(True)
            await release.wait()
            completed.append(True)

        first = [asyncio.create_task(send_reply(channel, network)) for _ in range(2)]
        waiting = None
        try:
            for _ in first:
                await asyncio.wait_for(entered.get(), 3)
            assert small_pool.pool.checkedout() == 2
            waiting = asyncio.create_task(send_reply(channel, network))
            # The third delivery must wait while both connections are sending.
            await asyncio.sleep(0.05)
            assert not waiting.done(), "waiting for a connection blocked the event loop"
            release.set()
            await asyncio.wait_for(asyncio.gather(*first, waiting), 3)
            assert len(completed) == 3
        finally:
            release.set()
            await asyncio.gather(
                *first, *([waiting] if waiting else []), return_exceptions=True
            )

    asyncio.run(exercise())


@pytest.mark.parametrize("channel", ["web", "feishu"])
@pytest.mark.parametrize("cancel", ["none", "task", "disconnect"])
def test_row_lock_wait_keeps_loop_alive_and_cleans_up(
    send_reply: Callable,
    small_pool: Engine,
    db: Session,
    demo: tuple,
    channel: str,
    cancel: str,
) -> None:
    org_id = demo[1]["E02"].org_id
    waiting = ThreadEvent()

    def query(_conn: object, _cursor: object, statement: str, *_args: object) -> None:
        if "FOR SHARE" in statement:
            waiting.set()

    event.listen(small_pool, "before_cursor_execute", query)

    async def exercise() -> None:
        sent = []
        scopes = []

        async def network() -> None:
            sent.append(True)

        async def send() -> None:
            with anyio.CancelScope() as scope:
                scopes.append(scope)
                await send_reply(channel, network)

        with Session(db.get_bind()) as writer:
            writer.exec(
                select(Organization).where(Organization.id == org_id).with_for_update()
            ).one()

            async def release_lock() -> None:
                assert await asyncio.to_thread(waiting.wait, 3)
                await asyncio.sleep(0.05)
                if cancel == "task":
                    task.cancel()
                elif cancel == "disconnect":
                    scopes[0].cancel()
                await asyncio.to_thread(writer.rollback)

            task = asyncio.create_task(send())
            unlock = asyncio.create_task(release_lock())
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(task, unlock, return_exceptions=True), 3
                )
                if cancel == "task":
                    assert isinstance(results[0], asyncio.CancelledError)
                    assert sent == []
                else:
                    assert results == [None, None]
                    assert sent == ([True] if cancel == "none" else [])
            finally:
                writer.rollback()
                await asyncio.gather(task, unlock, return_exceptions=True)

    try:
        asyncio.run(exercise())
    finally:
        event.remove(small_pool, "before_cursor_execute", query)


@pytest.mark.parametrize("channel", ["web", "feishu"])
@pytest.mark.parametrize("failure", ["error", "cancel", "disconnect"])
def test_failed_send_releases_lock_connection_and_buttons(
    send_reply: Callable,
    small_pool: Engine,
    demo: tuple,
    channel: str,
    failure: str,
) -> None:
    async def exercise() -> None:
        entered = asyncio.Event()

        async def network() -> None:
            entered.set()
            if failure == "error":
                raise RuntimeError("synthetic network failure")
            await asyncio.Event().wait()

        if failure == "disconnect":
            async with anyio.create_task_group() as group:
                group.start_soon(send_reply, channel, network)
                await entered.wait()
                group.cancel_scope.cancel()
        else:
            task = asyncio.create_task(send_reply(channel, network))
            await entered.wait()
            if failure == "cancel":
                task.cancel()
            with pytest.raises(
                RuntimeError if failure == "error" else asyncio.CancelledError
            ):
                await task

    asyncio.run(exercise())
    org_id = demo[1]["E02"].org_id
    with Session(small_pool) as check:
        check.exec(
            select(Organization)
            .where(Organization.id == org_id)
            .with_for_update(nowait=True)
        ).one()
        assert not check.exec(select(ImAction).where(ImAction.org_id == org_id)).all()


@pytest.mark.parametrize("failure", ["error", "cancel"])
def test_im_commit_wait_is_joined_before_session_close(
    send_reply: Callable, small_pool: Engine, demo: tuple, failure: str
) -> None:
    committing = ThreadEvent()
    release = ThreadEvent()
    finished = ThreadEvent()

    def before_commit(session: Session) -> None:
        if session.get_bind() is small_pool:
            committing.set()
            assert release.wait(3), "event loop could not release commit worker"
            if failure == "error":
                raise RuntimeError("synthetic commit failure")

    def after_commit(session: Session) -> None:
        if session.get_bind() is small_pool:
            finished.set()

    async def exercise() -> None:
        async def network() -> None:
            pass

        task = asyncio.create_task(send_reply("feishu", network))
        try:
            assert await asyncio.to_thread(committing.wait, 3)
            if failure == "cancel":
                task.cancel()
                await asyncio.sleep(0.05)
                task.cancel()
                assert not task.done(), (
                    "Session closed while its commit was still running"
                )
            release.set()
            with pytest.raises(
                RuntimeError if failure == "error" else asyncio.CancelledError
            ):
                await task
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    event.listen(Session, "before_commit", before_commit)
    event.listen(Session, "after_commit", after_commit)
    try:
        asyncio.run(exercise())
    finally:
        event.remove(Session, "before_commit", before_commit)
        event.remove(Session, "after_commit", after_commit)
    assert finished.is_set() == (failure == "cancel")
    with Session(small_pool) as check:
        org_id = demo[1]["E02"].org_id
        check.exec(
            select(Organization)
            .where(Organization.id == org_id)
            .with_for_update(nowait=True)
        ).one()
        rows = check.exec(select(ImAction).where(ImAction.org_id == org_id)).all()
        assert len(rows) == (1 if failure == "cancel" else 0)
