import asyncio
from dataclasses import replace
from datetime import timedelta
from threading import Event as ThreadEvent
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlmodel import Session

from app.core.db import engine
from app.domains.base import utc_now
from app.domains.bot import admission, core
from app.domains.im import actions, binding, gateway
from app.domains.im.adapters.base import Adapter, Attachment, Event, ImError
from app.domains.im.gateway import Gateway
from app.domains.im.models import ImSetting
from app.domains.model import pi_client
from app.domains.model.client import ModelUnavailable
from app.domains.model.models import ModelSetting
from tests.domains.test_bot_core import enable as enable_model
from tests.domains.test_im_gateway import principal

ACK = "收到，正在处理，请稍等"
WAIT = "还在处理，请再等一会儿"


@pytest.fixture(params=["feishu", "wecom"])
def im(
    demo: tuple,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> tuple:
    platform = request.param
    person = demo[1]["E02"]
    p = principal(db, person.org_id, person.id)
    setting = ImSetting(
        org_id=p.org_id,
        platform=platform,
        enabled=True,
        credentials="synthetic",
        bot_identity="synthetic-bot",
        email_auto_bind=False,
        updated_by_user_id=p.user_id,
    )
    db.add(setting)
    db.commit()
    assert binding.bind(
        db,
        p.org_id,
        platform,
        "user",
        "chat",
        "synthetic-bot",
        binding.create_code(db, p).code,
    )
    enable_model(db, p.org_id)
    sent: list[str] = []
    adapter = Mock(spec=Adapter)

    async def send(_chat: str, text: str) -> None:
        sent.append(text)

    adapter.send_text = AsyncMock(side_effect=send)
    adapter.render = AsyncMock(
        side_effect=lambda _chat, text, _buttons: sent.append(text) or "reply"
    )

    monkeypatch.setattr(core, "supports_images", lambda _: False)
    event = Event(p.org_id, platform, uuid4().hex, "user", "chat", True, "合成问题")
    return p, event, adapter, sent


def test_receipt_precedes_model_and_reply(
    im: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, event, adapter, sent = im

    def model(*_args: object, **_kwargs: object) -> pi_client.TurnResult:
        assert sent == [ACK]
        return pi_client.TurnResult(
            text="处理完成", stop_reason="stop", model_calls=1, tool_calls=0
        )

    monkeypatch.setattr(pi_client, "run_turn", model)
    g = Gateway(engine)

    async def run() -> None:
        await g.handle(event, "synthetic-bot", "synthetic", adapter)
        assert sent == [ACK, "处理完成"]
        await g.handle(event, "synthetic-bot", "synthetic", adapter)
        assert sent == [ACK, "处理完成"]

    asyncio.run(run())


@pytest.mark.parametrize(
    "outcome", ["success", "unavailable", "deadline", "unexpected"]
)
@pytest.mark.parametrize("slow", [False, True])
def test_reminder_once_and_never_after_completion(
    im: tuple, monkeypatch: pytest.MonkeyPatch, slow: bool, outcome: str
) -> None:
    _, event, adapter, sent = im
    release = ThreadEvent()
    original_sleep = asyncio.sleep

    async def run() -> None:
        started, ticking, elapsed, reminded = (asyncio.Event() for _ in range(4))
        loop = asyncio.get_running_loop()
        cancelled = []

        async def sleep(seconds: float) -> None:
            assert seconds == 30
            ticking.set()
            try:
                await elapsed.wait()
            except asyncio.CancelledError:
                cancelled.append(True)
                raise

        async def send(_chat: str, text: str) -> None:
            sent.append(text)
            if text == WAIT:
                reminded.set()

        def model(*_args: object, **_kwargs: object) -> pi_client.TurnResult:
            assert sent == [ACK]
            loop.call_soon_threadsafe(started.set)
            assert release.wait(5)
            if outcome == "unexpected":
                raise RuntimeError("synthetic failure")
            if outcome != "success":
                raise ModelUnavailable(code=outcome)
            return pi_client.TurnResult(
                text="处理完成", stop_reason="stop", model_calls=1, tool_calls=0
            )

        adapter.send_text = AsyncMock(side_effect=send)
        monkeypatch.setattr(gateway.asyncio, "sleep", sleep)
        monkeypatch.setattr(pi_client, "run_turn", model)
        task = asyncio.create_task(
            Gateway(engine).handle(event, "synthetic-bot", "synthetic", adapter)
        )
        try:
            await asyncio.wait_for(started.wait(), 5)
            await asyncio.wait_for(ticking.wait(), 5)
            if slow:
                elapsed.set()
                await asyncio.wait_for(reminded.wait(), 5)
                # Advancing again cannot produce a second reminder.
                await original_sleep(0)
                assert sent == [ACK, WAIT]
            release.set()
            if outcome == "unexpected":
                with pytest.raises(RuntimeError, match="synthetic failure"):
                    await task
            else:
                await task
                assert sent[-1] not in (ACK, WAIT)
            completed = sent.copy()
            elapsed.set()
            await original_sleep(0)
            await original_sleep(0)
            assert sent == completed
            assert sent.count(WAIT) == int(slow)
            assert cancelled == ([] if slow else [True])
        finally:
            release.set()
            if not task.done():
                await task

    asyncio.run(run())


@pytest.mark.parametrize(
    "reason",
    [
        "unbound",
        "disabled",
        "group",
        "empty",
        "whitespace",
        "long_text",
        "attachment_error",
        "invalid_file",
        "binding",
        "callback",
        "hourly_limit",
        "quota",
        "org_busy",
        "session_busy",
    ],
)
def test_no_receipt_without_admission(
    im: tuple, db: Session, monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    p, event, adapter, sent = im
    if reason == "unbound":
        event = replace(event, external_user_id="unknown")
    elif reason == "disabled":
        setting = db.get(ImSetting, (p.org_id, event.platform))
        setting.enabled = False
        db.add(setting)
        db.commit()
    elif reason == "group":
        event = replace(event, private=False)
    elif reason in ("empty", "whitespace", "long_text"):
        event = replace(
            event,
            text={"empty": "", "whitespace": "  ", "long_text": "文" * 10001}[reason],
        )
    elif reason == "attachment_error":
        event = replace(event, attachment_error="文件不可用")
    elif reason == "invalid_file":
        event = replace(event, attachment=Attachment("file", "bad.exe", b"synthetic"))
    elif reason == "binding":
        event = replace(event, text="绑定 000000")
    elif reason == "callback":
        event = replace(event, callback_id="invalid", token="invalid")
    elif reason == "hourly_limit":
        monkeypatch.setattr(admission, "HOURLY_TURNS", 0)
    elif reason == "quota":
        model_setting = db.get(ModelSetting, p.org_id)
        model_setting.paused_until = utc_now() + timedelta(minutes=10)
        db.add(model_setting)
        db.commit()
    elif reason == "org_busy":
        monkeypatch.setattr(admission, "ORG_TURNS", 0)
    elif reason == "session_busy":
        session = actions.conversation(db, p, event, "synthetic-bot")
        session.lease_until = utc_now() + timedelta(minutes=1)
        db.add(session)
        db.commit()
    model = Mock(side_effect=AssertionError("must not call model"))
    monkeypatch.setattr(pi_client, "run_turn", model)
    asyncio.run(Gateway(engine).handle(event, "synthetic-bot", "synthetic", adapter))
    assert ACK not in sent and WAIT not in sent
    model.assert_not_called()


def test_failed_receipt_does_not_stop_reply(
    im: tuple, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _, event, adapter, sent = im
    adapter.send_text = AsyncMock(side_effect=ImError("private-upstream-body"))
    model = Mock(
        return_value=pi_client.TurnResult(
            text="处理完成", stop_reason="stop", model_calls=1, tool_calls=0
        )
    )
    monkeypatch.setattr(pi_client, "run_turn", model)
    asyncio.run(Gateway(engine).handle(event, "synthetic-bot", "synthetic", adapter))
    assert sent == ["处理完成"]
    model.assert_called_once()
    assert "Bot processing receipt failed" in caplog.text
    assert "private-upstream-body" not in caplog.text and event.text not in caplog.text


def test_optional_callback_failure_is_ignored(
    im: tuple,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    p, event, _, sent = im
    monkeypatch.setattr(
        pi_client,
        "run_turn",
        Mock(
            return_value=pi_client.TurnResult(
                text="处理完成", stop_reason="stop", model_calls=1, tool_calls=0
            )
        ),
    )
    callback = Mock(side_effect=RuntimeError("private-callback-body"))
    reply = actions.text(db, p, event, "synthetic-bot", on_started=callback)
    callback.assert_called_once_with()
    unchanged = actions.text(db, p, event, "synthetic-bot")
    assert reply.response == unchanged.response
    assert sent == []
    assert "private-callback-body" not in caplog.text


def test_failed_reminder_does_not_stop_reply(
    im: tuple, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _, event, adapter, sent = im
    attempted = ThreadEvent()

    async def sleep(seconds: float) -> None:
        assert seconds == 30

    async def send(_chat: str, text: str) -> None:
        if text == WAIT:
            attempted.set()
            raise ImError("private-reminder-body")
        sent.append(text)

    def model(*_args: object, **_kwargs: object) -> pi_client.TurnResult:
        assert attempted.wait(5)
        return pi_client.TurnResult(
            text="处理完成", stop_reason="stop", model_calls=1, tool_calls=0
        )

    adapter.send_text = AsyncMock(side_effect=send)
    monkeypatch.setattr(gateway.asyncio, "sleep", sleep)
    monkeypatch.setattr(pi_client, "run_turn", model)
    asyncio.run(Gateway(engine).handle(event, "synthetic-bot", "synthetic", adapter))
    assert sent == [ACK, "处理完成"]
    assert "IM processing reminder failed" in caplog.text
    assert "private-reminder-body" not in caplog.text
