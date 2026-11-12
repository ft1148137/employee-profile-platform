import asyncio
import json
import logging
from base64 import b64decode
from collections.abc import AsyncIterator
from dataclasses import replace
from io import BytesIO
from queue import Queue
from uuid import uuid4

import httpx
import pytest
import sentry_sdk
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from sqlmodel import Session, select

from app.core.db import engine
from app.core.secrets import decrypt_secret, encrypt_secret
from app.domains.bot import attachments, core, store
from app.domains.bot import schemas as s
from app.domains.bot.models import BotAttachment
from app.domains.change_request.models import ProfileChangeRequest, ReviewDecision
from app.domains.im import actions, binding, gateway
from app.domains.im.adapters import feishu
from app.domains.im.adapters.base import Event
from app.domains.im.adapters.feishu import Feishu
from app.domains.im.models import ImAction, ImBinding, ImSetting
from app.domains.model import pi_client
from app.domains.org.models import Employee, EmploymentStatus, OrgMembership
from app.models import User
from tests.api.routes.test_employee_requests import payload
from tests.domains.test_bot_core import enable as enable_model
from tests.domains.test_bot_reviews import prepared
from tests.domains.test_chat_attachments import document
from tests.domains.test_im_gateway import principal
from tests.utils.feishu_listener import fake_listener, message
from tests.utils.pi import PiHarness

CREDS = {"app_id": "synthetic-app", "app_secret": "synthetic-secret"}


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
        elif "/resources/" in request.url.path:
            assert request.headers["authorization"] == "Bearer private-token"
            assert request.url.params["type"] == state["resource_kind"]
            if "resource_stream" in state:
                return httpx.Response(
                    200,
                    stream=state["resource_stream"],
                    headers=state.get("resource_headers", {}),
                )
            return httpx.Response(
                state.get("resource_status", 200),
                content=state["resource"],
            )
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


def attachment_message(ext: str, *, group: bool = False) -> dict:
    data = message("group" if group else "p2p")
    image = ext in ("png", "jpg", "gif", "webp")
    data["event"]["message"].update(
        message_id="message-synthetic",
        message_type="image" if image else "file",
        content=json.dumps(
            {"image_key": "private-resource-key"}
            if image
            else {"file_key": "private-resource-key", "file_name": f"材料.{ext}"}
        ),
    )
    return data


def pdf_material() -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(100, 100)
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {
                    NameObject("/F1"): writer._add_object(
                        DictionaryObject(
                            {
                                NameObject("/Type"): NameObject("/Font"),
                                NameObject("/Subtype"): NameObject("/Type1"),
                                NameObject("/BaseFont"): NameObject("/Helvetica"),
                            }
                        )
                    )
                }
            )
        }
    )
    content = DecodedStreamObject()
    content.set_data(b"BT /F1 12 Tf 10 50 Td (Synthetic material) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(content)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.mark.parametrize(
    "ext",
    [
        "txt",
        "docx",
        "pdf",
        "json",
        "png",
        "jpg",
        "gif",
        "webp",
        "image_fallback",
        "with_text",
    ],
)
def test_attachment_turn(
    demo: tuple,
    db: Session,
    service: dict,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    ext: str,
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
    images = {
        "png": b"\x89PNG\r\n\x1a\n",
        "jpg": b"\xff\xd8\xff",
        "gif": b"GIF89a",
        "webp": b"RIFF0000WEBP",
    }
    actual = "png" if ext == "image_fallback" else "txt" if ext == "with_text" else ext
    raw = (
        pdf_material()
        if actual == "pdf"
        else document(actual)
        if actual == "docx"
        else images.get(actual, "合成材料".encode())
    )
    service.update(resource=raw, resource_kind="image" if actual in images else "file")
    calls = []

    def run_turn(*_args: object, **kwargs: object) -> pi_client.TurnResult:
        calls.append(kwargs)
        return pi_client.TurnResult(
            text="已读取材料", stop_reason="stop", model_calls=1, tool_calls=0
        )

    monkeypatch.setattr(pi_client, "run_turn", run_turn)
    monkeypatch.setattr(core, "supports_images", lambda _: ext != "image_fallback")
    caplog.set_level(logging.DEBUG)

    async def run() -> None:
        adapter = Feishu(p.org_id, CREDS)
        try:
            incoming = await adapter._event(json.dumps(attachment_message(actual)))
            assert incoming and incoming.attachment
            if ext == "with_text":
                incoming = replace(incoming, text="请整理材料")
            g = gateway.Gateway(engine)
            await g.handle(incoming, CREDS["app_id"], setting.credentials, adapter)
            await g.handle(incoming, CREDS["app_id"], setting.credentials, adapter)
        finally:
            await adapter.close()

    asyncio.run(run())
    assert len(calls) == 1
    saved = db.exec(select(BotAttachment).where(BotAttachment.org_id == p.org_id)).one()
    assert b64decode(decrypt_secret(saved.ciphertext)) == raw
    session = actions.conversation(
        db,
        p,
        Event(p.org_id, "feishu", "lookup", external_chat_id="oc_test"),
        CREDS["app_id"],
    )
    assert saved.session_id == session.id
    history = store.messages_for(db, session)
    assert history[0].payload["text"] == (
        "请整理材料" if ext == "with_text" else f"我发送了文件：{saved.filename}"
    )
    assert history[0].payload["attachments"][0]["id"] == str(saved.id)
    if actual in images:
        assert bool(calls[0]["images"]) == (ext != "image_fallback")
        assert (
            attachments.IMAGE_NOTICE in history[-1].payload["messages"][0]["text"]
        ) == (ext == "image_fallback")
    else:
        assert attachments.START in calls[0]["input"]
        assert ("Synthetic material" if actual == "pdf" else "合成材料") in calls[0][
            "input"
        ]
    other_session = store.create_session(db, p)
    with pytest.raises(attachments.AttachmentError):
        attachments.prepare(db, p, "bot", other_session.id, [saved.id], "材料")
    other = principal(db, p.org_id, demo[1]["E01"].id)
    for wrong in (other, replace(p, org_id=uuid4())):
        with pytest.raises(store.BotSessionNotFound):
            attachments.prepare(db, wrong, "bot", session.id, [saved.id], "材料")
    wire = (
        json.dumps([m.payload for m in history], ensure_ascii=False)
        + caplog.text
        + json.dumps(service["requests"][-1])
    )
    for secret in (
        "private-token",
        "private-resource-key",
        "synthetic-secret",
        "/resources/",
    ):
        assert secret not in wire


@pytest.mark.parametrize(
    "case",
    ["unsupported", "large", "permission", "quota", "group", "unbound", "bad_image"],
)
def test_attachment_rejected(
    demo: tuple,
    db: Session,
    service: dict,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    case: str,
) -> None:
    p, setting = setup(db, demo)
    if case != "unbound":
        assert binding.bind(
            db,
            p.org_id,
            "feishu",
            "ou_test",
            "oc_test",
            CREDS["app_id"],
            binding.create_code(db, p).code,
        )
    service.update(
        resource=b"synthetic", resource_kind="image" if case == "bad_image" else "file"
    )
    if case == "permission":
        service["resource_status"] = 403
    elif case == "large":
        service["resource"] = b"x" * (10 * 1024 * 1024 + 1)
    elif case == "quota":
        monkeypatch.setattr(attachments, "USER_LIMIT", 0)
    caplog.set_level(logging.DEBUG)

    async def run() -> None:
        adapter = Feishu(p.org_id, CREDS)
        try:
            incoming = await adapter._event(
                json.dumps(
                    attachment_message(
                        "exe"
                        if case == "unsupported"
                        else "png"
                        if case == "bad_image"
                        else "txt",
                        group=case == "group",
                    )
                )
            )
            assert incoming
            await gateway.Gateway(engine).handle(
                incoming, CREDS["app_id"], setting.credentials, adapter
            )
        finally:
            await adapter.close()

    asyncio.run(run())
    expected = {
        "unsupported": "不支持的附件类型",
        "large": "文件太大",
        "permission": "无法读取文件，请让管理员在飞书开放平台开通文件权限",
        "quota": "附件总量已达上限",
        "group": "请私聊我",
        "unbound": binding.GUIDE,
        "bad_image": "不支持的附件类型",
    }[case]
    assert expected in json.loads(service["requests"][-1][1]["content"])["text"]
    assert not db.exec(
        select(BotAttachment).where(BotAttachment.org_id == p.org_id)
    ).all()
    if case in ("unsupported", "group"):
        assert not any("/resources/" in path for path, _ in service["requests"])
    assert (
        "private-token" not in caplog.text and "private-resource-key" not in caplog.text
    )


@pytest.mark.parametrize("case", ["header", "unknown", "short_header", "network"])
def test_download_is_bounded_and_closed(service: dict, case: str) -> None:
    class Resource(httpx.AsyncByteStream):
        reads = 0
        closed = False

        async def __aiter__(self) -> AsyncIterator[bytes]:
            self.reads += 1
            if case == "network":
                raise httpx.ReadError("private-upstream-token")
            yield b"x" * (5 * 1024 * 1024)
            self.reads += 1
            yield b"x" * (64 * 1024)
            pytest.fail("Do not read beyond the image limit")

        async def aclose(self) -> None:
            self.closed = True

    resource = Resource()
    service.update(resource_kind="image", resource_stream=resource)
    if case in ("header", "short_header"):
        service["resource_headers"] = {
            "content-length": str(5 * 1024 * 1024 + 1) if case == "header" else "1"
        }

    async def run() -> None:
        adapter = Feishu(uuid4(), CREDS)
        try:
            incoming = await adapter._event(json.dumps(attachment_message("png")))
            assert incoming and incoming.attachment is None
            assert incoming.attachment_error == (
                "无法读取文件，请稍后重试"
                if case == "network"
                else "文件太大，图片最多 5 MiB，文件最多 10 MiB"
            )
            assert resource.reads == (
                0 if case == "header" else 1 if case == "network" else 2
            )
            assert resource.closed
        finally:
            await adapter.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "case",
    [
        "match",
        "mismatch",
        "disabled",
        "no_account",
        "inactive",
        "departed",
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
    elif case == "departed":
        person.employment_status = EmploymentStatus.INACTIVE
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
                count = len(service["requests"])
                await gateway.Gateway(engine).handle(
                    incoming, CREDS["app_id"], setting.credentials, adapter
                )
                assert len(service["requests"]) == count
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
            service["status"] = 403
            assert await adapter._event(json.dumps(click(row))) is None
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


def test_review_card_confirmation(demo: tuple, db: Session, service: dict) -> None:
    _, setting = setup(db, demo)
    owner, request, p, session, card, _ = prepared(db, demo)
    session.channel, session.external_chat_id, session.bot_identity = (
        "feishu",
        "oc_test",
        CREDS["app_id"],
    )
    db.add(session)
    core._save(db, session, [card])
    db.commit()
    for person, external in [(p, "ou_test"), (owner, "ou_owner")]:
        assert binding.bind(
            db,
            p.org_id,
            "feishu",
            external,
            "oc_test",
            CREDS["app_id"],
            binding.create_code(db, person).code,
        )

    async def run() -> None:
        adapter = Feishu(p.org_id, CREDS)
        g = gateway.Gateway(engine)
        try:
            incoming = await adapter._event(json.dumps(message()))
            await actions.deliver(
                db,
                p,
                incoming,
                CREDS["app_id"],
                actions.Reply(session.id, s.TurnResponse(messages=[card])),
                adapter,
            )
            wire = json.dumps(service["requests"], ensure_ascii=False)
            assert "审批决定复述" in wire and "确认决定" in wire and "继续修改" in wire
            assert "详情在网页审批页查看" in wire and "本人陈述" not in wire
            assert not db.exec(
                select(ReviewDecision).where(ReviewDecision.org_id == p.org_id)
            ).all()
            row = db.exec(
                select(ImAction).where(
                    ImAction.org_id == p.org_id,
                    ImAction.action["action"].astext == "confirm_review",
                )
            ).one()
            callback = await adapter._event(json.dumps(click(row)))
            await g.handle(
                replace(callback, external_user_id="ou_owner"),
                CREDS["app_id"],
                setting.credentials,
                adapter,
            )
            db.refresh(row)
            assert row.consumed_at is None
            await g.handle(callback, CREDS["app_id"], setting.credentials, adapter)
            await g.handle(
                replace(callback, event_id=uuid4().hex),
                CREDS["app_id"],
                setting.credentials,
                adapter,
            )
            decision = db.exec(
                select(ReviewDecision).where(ReviewDecision.org_id == p.org_id)
            ).one()
            assert (decision.via, decision.client_name) == ("im", "feishu")
            assert (
                db.get(ProfileChangeRequest, request.id, populate_existing=True).status
                == "approved"
            )
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
            async with asyncio.timeout(5):
                incoming = await anext(adapter.poll())
            assert incoming.private and incoming.text == "本人合成经历"
            process = adapter.process
        finally:
            await adapter.close()
        assert process._closed and adapter.process is None

    asyncio.run(run())
    assert service["requests"] == []
