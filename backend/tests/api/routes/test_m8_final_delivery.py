"""Unsent web/IM replies are checked against current personnel visibility."""

import asyncio
import json
from threading import Event as ThreadEvent
from typing import Literal

import httpx
import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select

from app.authz.deps import get_org_principal
from app.authz.policies import can_view_employee
from app.domains.account.service import revoke_role
from app.domains.bot import core, store, streaming
from app.domains.bot import schemas as s
from app.domains.bot.models import BotMessage
from app.domains.im import actions
from app.domains.im.adapters.base import Event
from app.domains.im.adapters.feishu import Feishu
from app.domains.im.models import ImAction
from app.domains.model.models import ModelSetting
from app.domains.model.pi_client import TurnResult, TurnStream
from app.domains.org.models import Department, OrgMembership, Role, RoleGrant
from app.domains.org.paths import department_paths
from app.domains.org.roster import single
from app.models import User
from tests.api.routes.test_m8_employee_session_visibility import (
    demo as demo,  # noqa: F401
)
from tests.api.routes.test_m8_session_scope import _headers
from tests.domains.test_im_gateway import principal

pytest_plugins = ("tests.api.routes.test_m8_coordination_tools",)
NOTICE = "人员范围有变化，请重新提问"


@pytest.fixture
def pending(coordination: tuple, db: Session, monkeypatch: pytest.MonkeyPatch) -> tuple:
    people, _, demand = coordination
    target = people["E04"]
    p = principal(db, demand.org_id, people["E05"].id)
    private = f"{target.display_name} 的合成个人负荷为 80%"
    paths = department_paths(
        db.exec(select(Department).where(Department.org_id == p.org_id)).all()
    )

    def personnel(change: str, *, blocked: bool = False) -> None:
        with Session(db.get_bind()) as other:
            setting = other.get_one(ModelSetting, p.org_id)
            admin = get_org_principal(
                p.org_id, other.get_one(User, setting.updated_by_user_id), other
            )

            def apply() -> None:
                if change == "revoke":
                    grants = other.exec(
                        select(RoleGrant)
                        .join(
                            OrgMembership, RoleGrant.membership_id == OrgMembership.id
                        )
                        .where(
                            RoleGrant.org_id == p.org_id,
                            OrgMembership.user_id == p.user_id,
                            RoleGrant.role == Role.DEPT_MANAGER,
                        )
                    ).all()
                    for grant in grants:
                        revoke_role(other, admin, grant.id, commit=False)
                    other.commit()
                else:
                    args = (
                        single.Transfer(
                            employee_id=target.id,
                            department=paths[people["E10"].department_id],
                        )
                        if change == "transfer"
                        else single.Offboard(employee_ids=[target.id])
                    )
                    single.apply(other, admin, args)

            if blocked:
                other.execute(text("SET LOCAL lock_timeout = '100ms'"))
                with pytest.raises(OperationalError) as error:
                    apply()
                assert error.value.orig.sqlstate == "55P03"
            else:
                apply()

    original_call = core.tools.call

    def call(*args: object, **kwargs: object) -> object:
        result = original_call(*args, **kwargs)
        args[3].append(
            s.PlanCard(
                title=private,
                fields=[s.CardField(label="个人负荷", value=private)],
                changes=private,
                link="/plans",
            )
        )
        args[3].append(
            s.CapacityCard(
                kind="demand_reply",
                title=private,
                content=private,
                fields=[s.CardField(label="人员", value=private)],
                arguments={"employee_id": str(target.id)},
                actions=[
                    s.CapacityAction(action="confirm_capacity", card_id=target.id)
                ],
            )
        )
        return result

    def model(_config: object, **kwargs: object) -> TurnResult:
        data = kwargs["on_tool_call"]("list_team_members", {})
        assert str(target.id) in json.dumps(data)
        return TurnResult(
            text=private,
            stop_reason="stop",
            model_calls=1,
            tool_calls=1,
            session_state={"messages": [{"private": private}]},
        )

    monkeypatch.setattr(core.tools, "call", call)
    monkeypatch.setattr(core.pi_client, "run_turn", model)
    return p, target, private, personnel


@pytest.mark.parametrize("channel", ["web", "feishu"])
@pytest.mark.parametrize("change", ["transfer", "offboard", "revoke", "unchanged"])
@pytest.mark.parametrize("when", ["model", "completed"])
def test_pending_reply(
    pending: tuple,
    db: Session,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    channel: Literal["web", "feishu"],
    change: str,
    when: str,
) -> None:
    p, target, private, personnel = pending
    session = store.create_session(db, p, channel=channel)
    original_model = core.pi_client.run_turn
    original_turn = core.turn

    def model(*args: object, **kwargs: object) -> TurnResult:
        result = original_model(*args, **kwargs)
        # Web's uncommitted user message holds an org FK lock until turn commit.
        if when == "model" and change != "unchanged":
            personnel(change, blocked=channel == "web")
        return result

    def turn(*args: object, **kwargs: object) -> s.TurnResponse:
        result = original_turn(*args, **kwargs)
        if change != "unchanged" and (when == "completed" or channel == "web"):
            personnel(change)
        return result

    monkeypatch.setattr(core.pi_client, "run_turn", model)
    monkeypatch.setattr(core, "turn", turn)
    reply = None
    if channel == "web":
        response = client.post(
            f"/api/v1/bot/sessions/{session.id}/turns",
            params={"org_id": str(p.org_id)},
            headers=_headers(db, p.org_id, p.employee_id),
            json={"text": "查看团队"},
        )
        assert response.status_code == 200, response.text
        events = [json.loads(line) for line in response.text.splitlines()]
        assert not any(e["type"] == "text" for e in events)
        assert events[-1]["type"] == "final", events
        sent = events[-1]["response"]
    else:
        incoming = Event(p.org_id, "feishu", "event", "user", "chat", True, "查看团队")
        reply = actions.text(db, p, incoming, "synthetic-app")
        session = store.get_session(db, p, reply.session_id)
        if when == "model" and change != "unchanged":
            assert reply.response.messages == [s.TextMessage(text=NOTICE)]
            assert reply.response.steps == []
        requests = []

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
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={"code": 0, "data": {"message_id": "sent"}})

        async def send() -> None:
            adapter = Feishu(
                p.org_id, {"app_id": "synthetic", "app_secret": "synthetic"}
            )
            await adapter.client.aclose()
            adapter.client = httpx.AsyncClient(
                base_url="https://open.feishu.cn/open-apis/",
                transport=httpx.MockTransport(capture),
            )
            try:
                await actions.deliver(db, p, incoming, "synthetic-app", reply, adapter)
            finally:
                await adapter.close()

        asyncio.run(send())
        assert requests
        sent = requests
    raw = json.dumps(sent, ensure_ascii=False)
    assert "_scope" not in raw and "_principal" not in raw
    if change == "unchanged":
        assert target.display_name in raw and "80%" in raw
        assert NOTICE not in raw
        return
    assert target.display_name not in raw and "80%" not in raw
    assert NOTICE in raw
    if channel == "web":
        assert sent == {
            "messages": [{"type": "text", "text": NOTICE, "review_link": None}],
            "steps": [],
        }
    else:
        assert len(sent) == 1
        assert "action" not in json.loads(sent[0]["content"])["elements"][0]
        assert reply.response.messages == [s.TextMessage(text=NOTICE)]
        assert reply.response.steps == []
        assert not db.exec(select(ImAction).where(ImAction.org_id == p.org_id)).all()
    db.expire_all()
    current = principal(db, p.org_id, p.employee_id)
    assert not can_view_employee(current, db.get_one(type(target), target.id))
    original = {
        m.id: m.ciphertext
        for m in db.exec(
            select(BotMessage).where(
                BotMessage.org_id == p.org_id, BotMessage.session_id == session.id
            )
        ).all()
    }
    assert private in str(store.messages_for(db, session))
    assert private not in str(store.list_messages(db, current, session.id))
    for id, ciphertext in original.items():
        assert db.get_one(BotMessage, id).ciphertext == ciphertext


@pytest.mark.parametrize("change", ["transfer", "offboard"])
@pytest.mark.parametrize("boundary", ["queued", "sending", "send_error"])
def test_web_final_send_boundary(
    pending: tuple,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
    boundary: str,
) -> None:
    p, target, _, personnel = pending
    session = store.create_session(db, p)
    completed = ThreadEvent()
    sent = []
    monkeypatch.setattr(streaming, "cancel_run", lambda _: None)

    def run(worker_db: Session, stream: TurnStream) -> s.TurnResponse:
        stream.on_text("安全的进度")
        result = core.turn(worker_db, p, session.id, "查看团队", stream=stream)
        completed.set()
        return result

    async def send(message: dict) -> None:
        if not message.get("body"):
            return
        event = json.loads(message["body"])
        sent.append(event)
        if event["type"] == "text":
            assert await asyncio.to_thread(completed.wait, 5)
            if boundary == "queued":
                await asyncio.to_thread(personnel, change)
        if event["type"] == "final" and boundary != "queued":
            # A real personnel transaction cannot commit between check and send.
            await asyncio.to_thread(personnel, change, blocked=True)
            if boundary == "send_error":
                raise RuntimeError("synthetic send failure")

    async def deliver() -> None:
        response = streaming.response(run, Request({"type": "http"}))
        if boundary == "send_error":
            with pytest.raises(RuntimeError, match="synthetic send failure"):
                await response.stream_response(send)
        else:
            await response.stream_response(send)

    asyncio.run(deliver())
    raw = json.dumps(sent[-1], ensure_ascii=False)
    assert sent[-1]["type"] == "final"
    if boundary == "queued":
        assert NOTICE in raw and target.display_name not in raw and "80%" not in raw
    else:
        assert target.display_name in raw and "80%" in raw
        # Success and failed ASGI sends both release their delivery transaction.
        personnel(change)


@pytest.mark.parametrize("change", ["transfer", "offboard"])
@pytest.mark.parametrize("send_error", [False, True])
def test_feishu_each_message_holds_visibility_lock(
    pending: tuple, db: Session, change: str, send_error: bool
) -> None:
    p, target, _, personnel = pending
    incoming = Event(p.org_id, "feishu", "event", "user", "chat", True, "查看团队")
    reply = actions.text(db, p, incoming, "synthetic-app")
    sent = []

    async def capture(request: httpx.Request) -> httpx.Response:
        if "/auth/" in request.url.path:
            return httpx.Response(
                200,
                json={"code": 0, "tenant_access_token": "synthetic", "expire": 7200},
            )
        sent.append(json.loads(request.content))
        # Includes cards after deliver committed each preceding message.
        await asyncio.to_thread(personnel, change, blocked=True)
        if send_error:
            raise RuntimeError("synthetic send failure")
        return httpx.Response(200, json={"code": 0, "data": {"message_id": "sent"}})

    async def deliver() -> None:
        adapter = Feishu(p.org_id, {"app_id": "synthetic", "app_secret": "synthetic"})
        await adapter.client.aclose()
        adapter.client = httpx.AsyncClient(
            base_url="https://open.feishu.cn/open-apis/",
            transport=httpx.MockTransport(capture),
        )
        try:
            if send_error:
                with pytest.raises(RuntimeError, match="synthetic send failure"):
                    await actions.deliver(
                        db, p, incoming, "synthetic-app", reply, adapter
                    )
            else:
                await actions.deliver(db, p, incoming, "synthetic-app", reply, adapter)
        finally:
            await adapter.close()

    asyncio.run(deliver())
    assert len(sent) == (1 if send_error else 3)
    assert target.display_name in json.dumps(sent, ensure_ascii=False)
    personnel(change)


@pytest.mark.parametrize("change", ["transfer", "offboard"])
def test_feishu_change_between_messages_clears_pending_cards(
    pending: tuple, db: Session, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    p, target, _, personnel = pending
    incoming = Event(p.org_id, "feishu", "event", "user", "chat", True, "查看团队")
    reply = actions.text(db, p, incoming, "synthetic-app")
    original_commit = db.commit
    commits = 0

    def commit() -> None:
        nonlocal commits
        original_commit()
        commits += 1
        if commits == 1:
            personnel(change)

    monkeypatch.setattr(db, "commit", commit)
    sent = []

    def capture(request: httpx.Request) -> httpx.Response:
        if "/auth/" in request.url.path:
            return httpx.Response(
                200,
                json={"code": 0, "tenant_access_token": "synthetic", "expire": 7200},
            )
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"code": 0, "data": {"message_id": "sent"}})

    async def deliver() -> None:
        adapter = Feishu(p.org_id, {"app_id": "synthetic", "app_secret": "synthetic"})
        await adapter.client.aclose()
        adapter.client = httpx.AsyncClient(
            base_url="https://open.feishu.cn/open-apis/",
            transport=httpx.MockTransport(capture),
        )
        try:
            await actions.deliver(db, p, incoming, "synthetic-app", reply, adapter)
        finally:
            await adapter.close()

    asyncio.run(deliver())
    assert len(sent) == 2
    assert target.display_name in sent[0]["content"]
    assert NOTICE in sent[1]["content"]
    assert (
        target.display_name not in sent[1]["content"]
        and "80%" not in sent[1]["content"]
    )
    assert reply.response.messages == [s.TextMessage(text=NOTICE)]
    assert reply.response.steps == []
    assert not db.exec(select(ImAction).where(ImAction.org_id == p.org_id)).all()
