"""Bound Feishu sends without weakening the personnel delivery boundary."""

import asyncio
import json
import time
from collections.abc import Generator
from threading import Event as ThreadEvent

import httpx
import pytest
from sqlalchemy import event
from sqlmodel import Session, create_engine, select

from app.authz.deps import get_org_principal
from app.domains.account.service import revoke_role
from app.domains.bot import delivery, store
from app.domains.bot import schemas as s
from app.domains.im import actions
from app.domains.im.adapters.base import Event, ImError
from app.domains.im.adapters.feishu import Feishu
from app.domains.im.models import ImAction
from app.domains.model.models import ModelSetting
from app.domains.org.models import OrgMembership, Role, RoleGrant
from app.models import User
from tests.api.routes.test_m8_employee_session_visibility import (
    demo as demo,  # noqa: F401
)
from tests.domains.test_im_gateway import principal


@pytest.fixture
def sending(db: Session, demo: tuple) -> Generator[tuple]:
    p = principal(db, demo[1]["E01"].org_id, demo[1]["E01"].id)
    session = store.create_session(db, p, channel="feishu")
    response = s.TurnResponse(
        messages=[
            s.CapacityCard(
                kind="demand_reply",
                title="合成人员信息",
                content="合成回复",
                fields=[],
                arguments={},
                actions=[
                    s.CapacityAction(action="confirm_capacity", card_id=session.id)
                ],
            ),
            s.TextMessage(text="不应在首条失败后发送"),
        ]
    )
    response._principal = p
    response._scope = store.session_scope(db, p)
    grant_id = db.exec(
        select(RoleGrant.id)
        .join(OrgMembership, RoleGrant.membership_id == OrgMembership.id)
        .where(
            RoleGrant.org_id == p.org_id,
            OrgMembership.user_id == p.user_id,
            RoleGrant.role == Role.DEPT_MANAGER,
        )
    ).one()
    admin_id = db.get_one(ModelSetting, p.org_id).updated_by_user_id
    reply = actions.Reply(session.id, response)
    incoming = Event(p.org_id, "feishu", "synthetic-event", "user", "chat", True)
    db.rollback()
    pool = create_engine(db.get_bind().url, pool_size=1, max_overflow=0, pool_timeout=1)
    try:
        yield p, reply, incoming, pool, grant_id, admin_id
        assert pool.pool.checkedout() == 0
    finally:
        pool.dispose()


@pytest.mark.parametrize("outcome", ["success", "error", "timeout"])
def test_send_releases_connection_and_pending_role_change(
    sending: tuple, db: Session, outcome: str
) -> None:
    p, reply, incoming, pool, grant_id, admin_id = sending
    writer_started = ThreadEvent()
    queries: list[str] = []
    attempts: list[dict] = []
    writer_elapsed: list[float] = []

    def query(_conn: object, _cursor: object, statement: str, *_args: object) -> None:
        if "FOR UPDATE" in statement:
            writer_started.set()

    def capture_query(
        _conn: object, _cursor: object, statement: str, *_args: object
    ) -> None:
        queries.append(statement)

    def revoke() -> None:
        started = time.monotonic()
        with Session(db.get_bind()) as writer:
            admin = get_org_principal(p.org_id, writer.get_one(User, admin_id), writer)
            revoke_role(writer, admin, grant_id)
        writer_elapsed.append(time.monotonic() - started)

    async def exercise() -> None:
        writer = None

        async def capture(request: httpx.Request) -> httpx.Response:
            nonlocal writer
            if "/auth/" in request.url.path:
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "tenant_access_token": "synthetic",
                        "expire": 7200,
                    },
                )
            attempts.append(json.loads(request.content))
            assert writer is None, "failed sends must not retry or send pending content"
            writer = asyncio.create_task(asyncio.to_thread(revoke))
            assert await asyncio.to_thread(writer_started.wait, 2)
            await asyncio.sleep(0.05)
            assert not writer.done(), "personnel changes must wait until send finishes"
            if outcome == "timeout":
                await asyncio.sleep(30)
            elif outcome == "error":
                raise ImError("network")
            return httpx.Response(200, json={"code": 0, "data": {"message_id": "sent"}})

        adapter = Feishu(p.org_id, {"app_id": "synthetic", "app_secret": "synthetic"})
        await adapter.client.aclose()
        adapter.client = httpx.AsyncClient(
            base_url="https://open.feishu.cn/open-apis/",
            transport=httpx.MockTransport(capture),
        )
        baseline = pool.pool.checkedout()
        started = time.monotonic()
        try:
            with Session(pool) as session:
                # One message on success; failures must leave the second unsent.
                if outcome == "success":
                    reply.response.messages = reply.response.messages[:1]
                    await actions.deliver(
                        session, p, incoming, "synthetic", reply, adapter
                    )
                else:
                    with pytest.raises(ImError) as error:
                        await asyncio.wait_for(
                            actions.deliver(
                                session, p, incoming, "synthetic", reply, adapter
                            ),
                            14,
                        )
                    assert error.value.code == (
                        "timeout" if outcome == "timeout" else "network"
                    )
                # Check before Session.close() can mask a leaked transaction.
                assert pool.pool.checkedout() == baseline
                ended = time.monotonic()
                assert writer is not None
                await asyncio.wait_for(writer, 2)
                assert time.monotonic() - ended < 1
                if outcome == "timeout":
                    assert 9 <= ended - started < 13
                    assert writer_elapsed[0] < 13
        finally:
            await adapter.close()
            if writer:
                await asyncio.wait_for(writer, 2)

    event.listen(db.get_bind(), "before_cursor_execute", query)
    event.listen(pool, "before_cursor_execute", capture_query)
    try:
        asyncio.run(exercise())
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", query)
        event.remove(pool, "before_cursor_execute", capture_query)
    assert len(attempts) == 1
    lock_index = next(
        i for i, statement in enumerate(queries) if "FOR SHARE" in statement
    )
    assert not any(
        table in statement
        for statement in queries[lock_index + 1 :]
        for table in ("bot_session", "bot_message", "bot_setting")
    ), "session and retention queries must finish before acquiring the personnel lock"
    with Session(pool) as check:
        rows = check.exec(select(ImAction).where(ImAction.org_id == p.org_id)).all()
        assert len(rows) == (1 if outcome == "success" else 0)


def test_committed_role_change_blocks_pending_content(
    sending: tuple, db: Session
) -> None:
    p, reply, incoming, pool, grant_id, admin_id = sending
    admin = get_org_principal(p.org_id, db.get_one(User, admin_id), db)
    revoke_role(db, admin, grant_id)
    sent: list[str] = []

    async def exercise() -> None:
        def capture(request: httpx.Request) -> httpx.Response:
            if "/auth/" in request.url.path:
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "tenant_access_token": "synthetic",
                        "expire": 7200,
                    },
                )
            sent.append(request.content.decode())
            return httpx.Response(200, json={"code": 0, "data": {"message_id": "sent"}})

        adapter = Feishu(p.org_id, {"app_id": "synthetic", "app_secret": "synthetic"})
        await adapter.client.aclose()
        adapter.client = httpx.AsyncClient(
            base_url="https://open.feishu.cn/open-apis/",
            transport=httpx.MockTransport(capture),
        )
        try:
            with Session(pool) as session:
                await actions.deliver(session, p, incoming, "synthetic", reply, adapter)
                assert pool.pool.checkedout() == 0
        finally:
            await adapter.close()

    asyncio.run(exercise())
    assert len(sent) == 1
    assert delivery.SCOPE_CHANGED in sent[0]
    assert "合成人员信息" not in sent[0]
    assert reply.response.steps == []
    assert reply.response.messages == [s.TextMessage(text=delivery.SCOPE_CHANGED)]
