import asyncio
import json
import logging
from dataclasses import replace
from queue import Queue
from threading import Event as Wait
from uuid import uuid4

import httpx
import pytest
import sentry_sdk
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.db import engine
from app.core.secrets import encrypt_secret
from app.domains.bot import core
from app.domains.change_request.models import ProfileChangeRequest
from app.domains.im import binding, gateway
from app.domains.im.adapters import feishu
from app.domains.im.adapters.feishu import Feishu
from app.domains.im.models import ImAction, ImBinding, ImSetting
from app.domains.org.models import Employee, OrgMembership
from app.models import User
from tests.api.routes.test_employee_requests import payload
from tests.domains.test_bot_core import enable as enable_model
from tests.domains.test_im_gateway import principal
from tests.utils.pi import PiHarness

CREDS = {"app_id": "synthetic-app", "app_secret": "synthetic-secret"}


def message(kind: str = "p2p") -> dict:
    return {
        "header": {"event_id": uuid4().hex, "event_type": "im.message.receive_v1"},
        "event": {
            "sender": {"sender_type": "user", "sender_id": {"open_id": "ou_test"}},
            "message": {
                "chat_id": "oc_test",
                "chat_type": kind,
                "message_type": "text",
                "content": json.dumps({"text": "本人合成经历"}),
            },
        },
    }


def click(row: ImAction) -> dict:
    return {
        "header": {"event_id": uuid4().hex, "event_type": "card.action.trigger"},
        "event": {
            "operator": {"open_id": "ou_test"},
            "action": {"value": {"token": row.token}},
            "context": {
                "open_message_id": row.message_id,
                "open_chat_id": row.external_chat_id,
            },
        },
    }


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> dict:
    state = {"email": None, "mode": "p2p", "requests": [], "status": 200, "code": 0}

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        state["requests"].append((request.url.path, body))
        if "/auth/" in request.url.path:
            data = {"tenant_access_token": "private-token", "expire": 7200}
        elif "/contact/" in request.url.path:
            data = {
                "data": {
                    "user": {
                        "enterprise_email": state["email"],
                        "email": "personal@example.com",
                    }
                }
            }
        elif "/chats/" in request.url.path:
            data = {"data": {"chat_mode": state["mode"]}}
        else:
            data = {"data": {"message_id": str(len(state["requests"]))}}
        return httpx.Response(
            state["status"],
            json={"code": state["code"], "msg": "private-upstream-body", **data},
        )

    original = httpx.AsyncClient.__init__

    def client(instance: httpx.AsyncClient, **kwargs: object) -> None:
        original(instance, **kwargs, transport=httpx.MockTransport(respond))

    monkeypatch.setattr(feishu.httpx.AsyncClient, "__init__", client)
    return state


def setup(db: Session, demo: tuple) -> tuple:
    person = demo[1]["E02"]
    p = principal(db, person.org_id, person.id)
    setting = ImSetting(
        org_id=p.org_id,
        platform="feishu",
        enabled=True,
        credentials=encrypt_secret(json.dumps(CREDS)),
        bot_identity=CREDS["app_id"],
        updated_by_user_id=p.user_id,
    )
    db.add(setting)
    db.commit()
    return p, setting


@pytest.mark.parametrize(
    "case",
    [
        "match",
        "mismatch",
        "disabled",
        "no_account",
        "inactive",
        "duplicate",
        "foreign",
        "personal",
    ],
)
def test_email_binding(demo: tuple, db: Session, service: dict, case: str) -> None:
    p, setting = setup(db, demo)
    person = db.get(Employee, p.employee_id)
    person.work_email = "Work@Example.com"
    service["email"] = "work@example.COM"
    if case == "mismatch":
        service["email"] = "unknown@example.com"
    elif case == "disabled":
        setting.email_auto_bind = False
    elif case == "personal":
        service["email"] = None
        person.work_email = "personal@example.com"
    elif case == "no_account":
        membership = db.exec(
            select(OrgMembership).where(
                OrgMembership.org_id == p.org_id,
                OrgMembership.employee_id == p.employee_id,
            )
        ).one()
        membership.employee_id = None
        db.add(membership)
    elif case == "inactive":
        user = db.get(User, p.user_id)
        user.is_active = False
        db.add(user)
    elif case == "duplicate":
        other = db.get(Employee, demo[1]["E01"].id)
        other.work_email = person.work_email.lower()
        db.add(other)
    elif case == "foreign":
        # This email exists only in another tenant; the local employee must not bind.
        service["email"] = "foreign-only@example.com"
        from app.domains.org.models import Organization

        foreign = db.exec(
            select(Organization).where(Organization.id != p.org_id)
        ).first()
        other = db.exec(select(Employee).where(Employee.org_id == foreign.id)).first()
        other.work_email = service["email"]
        db.add(other)
    db.add(person)
    db.add(setting)
    db.commit()

    async def run() -> None:
        adapter = Feishu(p.org_id, CREDS)
        try:
            incoming = await adapter._event(json.dumps(message()))
            await gateway.Gateway(engine).handle(
                incoming, CREDS["app_id"], setting.credentials, adapter
            )
            rows = db.exec(select(ImBinding).where(ImBinding.org_id == p.org_id)).all()
            assert bool(rows) == (case == "match")
            body = json.loads(service["requests"][-1][1]["content"])["text"]
            assert body == (
                "已按企业邮箱绑定，可以在网页的个人设置中解绑。请继续发送消息。"
                if case == "match"
                else binding.GUIDE
            )
            if rows:
                assert (
                    rows[0].bound_via == "email"
                    and rows[0].employee_id == p.employee_id
                )
            if case == "disabled":
                assert not any("/contact/" in path for path, _ in service["requests"])
        finally:
            await adapter.close()

    try:
        asyncio.run(run())
    finally:
        if case == "inactive":
            user.is_active = True
            db.add(user)
            db.commit()


def test_turn_submit_withdraw(
    demo: tuple,
    db: Session,
    service: dict,
    pi_service: PiHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    p, setting = setup(db, demo)
    assert binding.bind(
        db,
        p.org_id,
        "feishu",
        "ou_test",
        "oc_test",
        CREDS["app_id"],
        binding.create_code(db, p).code,
    )
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
        adapter = Feishu(p.org_id, CREDS)
        g = gateway.Gateway(engine)

        async def pump(data: dict) -> None:
            incoming = await adapter._event(json.dumps(data))
            assert incoming
            await g.handle(incoming, CREDS["app_id"], setting.credentials, adapter)

        try:
            await pump(message())
            row = db.exec(select(ImAction).where(ImAction.org_id == p.org_id)).first()
            assert row and len(row.token) == 22
            card = json.loads(service["requests"][-1][1]["content"])
            assert card["elements"][-1]["actions"][0]["value"] == {"token": row.token}
            # Wrong sender, forwarded group, and foreign tenant cannot consume this action.
            incoming = await adapter._event(json.dumps(click(row)))
            for invalid in [
                replace(incoming, external_user_id="ou_other"),
                replace(incoming, org_id=uuid4()),
            ]:
                await g.handle(invalid, CREDS["app_id"], setting.credentials, adapter)
            service["mode"] = "group"
            await pump(click(row))
            db.refresh(row)
            assert row.consumed_at is None
            assert (
                json.loads(service["requests"][-1][1]["content"])["text"] == "请私聊我"
            )
            service["mode"] = "p2p"
            await pump(click(row))
            request = db.get(ProfileChangeRequest, row.action["request_id"])
            assert (request.status, request.client_name) == (
                "pending_review",
                "内置机器人（飞书）",
            )
            await pump(click(row))
            withdraw = next(
                r
                for r in db.exec(
                    select(ImAction).where(ImAction.org_id == p.org_id)
                ).all()
                if r.action["action"] == "withdraw"
            )
            await pump(click(withdraw))
            db.refresh(request)
            assert request.status == "withdrawn"
            assert not any("/contact/" in path for path, _ in service["requests"])
        finally:
            await adapter.close()

    asyncio.run(run())


def test_group_and_rendering(demo: tuple, db: Session, service: dict) -> None:
    p, setting = setup(db, demo)

    async def run() -> None:
        adapter = Feishu(p.org_id, CREDS)
        try:
            incoming = await adapter._event(json.dumps(message("group")))
            await gateway.Gateway(engine).handle(
                incoming, CREDS["app_id"], setting.credentials, adapter
            )
            assert len(service["requests"]) == 2
            assert (
                json.loads(service["requests"][-1][1]["content"])["text"] == "请私聊我"
            )
            assert await adapter._event("{}") is None
            assert await adapter.enterprise_email("ou_test") is None
            for text, buttons in [
                ("改动预览" + "长" * 12000, [("确认提交", "short-token")]),
                ("请求状态", [("撤回", "short-token")]),
                ("待确认问题", [("回答", "short-token")]),
                ("普通文本", []),
            ]:
                await adapter.render("oc_test", text, buttons)
                card = json.loads(service["requests"][-1][1]["content"])
                assert card["elements"][0]["text"]["content"] == (
                    text
                    if len(text) <= 3000
                    else "内容较长，请在网页查看完整内容并操作。"
                )
                if len(text) > 3000:
                    assert len(card["elements"]) == 1
        finally:
            await adapter.close()

    asyncio.run(run())


def test_connection(
    demo: tuple,
    client: TestClient,
    service: dict,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    base, _, auth = demo
    url = base + "/im-settings/feishu"
    client.put(url, headers=auth["admin"], json={"credentials": CREDS})
    caplog.set_level(logging.DEBUG)
    with sentry_sdk.init(dsn="", default_integrations=True):

        def unexpected_release() -> None:
            pytest.fail("Feishu must not auto-detect Sentry release")

        monkeypatch.setattr("sentry_sdk.client.get_default_release", unexpected_release)
        for status, code, expected in [
            (200, 0, "连接成功"),
            (401, 0, "应用凭据无效或没有权限"),
            (200, 10003, "应用凭据无效或没有权限"),
            (500, 0, "平台服务暂时不可用"),
        ]:
            service.update(status=status, code=code)
            with sentry_sdk.isolation_scope() as scope:
                response = client.post(url + "/test", headers=auth["admin"])
                assert response.json()["message"] == expected
                output = (
                    response.text
                    + caplog.text
                    + json.dumps(scope._breadcrumbs, default=str)
                )
                assert all(
                    secret not in output
                    for secret in [
                        *CREDS.values(),
                        "private-token",
                        "private-upstream-body",
                    ]
                )


def fake_listener(_credentials: dict, events: object) -> None:
    events.put_nowait(("event", json.dumps(message())))
    Wait().wait(30)


def test_sdk_callbacks_and_process_cleanup(
    service: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    import lark_oapi as lark
    from lark_oapi.ws.exception import ClientException

    queue = Queue()
    queue.close = queue.join_thread = lambda: None
    data = message()
    data["schema"] = "2.0"

    def start(sdk: object) -> None:
        sdk._event_handler._do_without_validation(json.dumps(data).encode())
        action = {
            "schema": "2.0",
            "header": {"event_id": "callback", "event_type": "card.action.trigger"},
            "event": {},
        }
        result = sdk._event_handler._do_without_validation(json.dumps(action).encode())
        assert result is not None
        raise ClientException(1, "private-upstream-body")

    monkeypatch.setattr(lark.ws.Client, "start", start)
    monkeypatch.setattr("lark_oapi.ws.client.ExpiringCache", lambda **_: None)
    disabled = logging.root.manager.disable
    try:
        feishu._listen(CREDS, queue)
    finally:
        logging.disable(disabled)
    assert [queue.get_nowait()[0] for _ in range(3)] == ["event", "event", "error"]
    monkeypatch.setattr(feishu, "_listen", fake_listener)

    async def run() -> None:
        adapter = Feishu(uuid4(), CREDS)
        try:
            async with asyncio.timeout(15):
                incoming = await anext(adapter.poll())
            assert incoming.private and incoming.text == "本人合成经历"
            process = adapter.process
        finally:
            await adapter.close()
        assert process._closed and adapter.process is None

    asyncio.run(run())
    assert service["requests"] == []
