import json
import socket
import time
from collections.abc import Generator
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import settings
from app.domains.base import utc_now
from app.domains.bot import admission, core, store, tools
from app.domains.model import pi_client
from app.domains.model.client import ModelUnavailable
from app.main import app
from tests.api.routes.test_employee_requests import principal_for
from tests.domains.test_bot_core import enable
from tests.utils.bot import final_response

pytestmark = pytest.mark.xdist_group("model")


@pytest.fixture
def fake_pi(monkeypatch: pytest.MonkeyPatch) -> Generator[Event]:
    cancelled = Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: Any) -> None:
            pass

        def do_DELETE(self) -> None:
            cancelled.set()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.end_headers()
            if self.path != "/v1/turns":
                self.wfile.write(b"{}")
                return
            admin = any(t["name"] == "admin_read" for t in body["tools"])
            drafting = admin and body["input"] == "停止"
            tool = {
                "type": "tool_call",
                "name": "propose_changes"
                if drafting
                else "admin_read"
                if admin
                else "get_my_profile",
                "arguments": {
                    "items": [
                        {
                            "function": "update_update_settings",
                            "args": {"reminder_schedule": "yearly"},
                        }
                    ]
                }
                if drafting
                else {"function": "list_roles", "args": {}}
                if admin
                else {},
                "call_id": "test",
            }
            events = [
                *([tool] if drafting else []),
                {"type": "text", "text": "第一段"},
                *([] if drafting else [tool]),
                {"type": "text", "text": "第二段"},
                {"type": "text", "text": "第三段"},
                {
                    "type": "done",
                    "stopReason": "stop",
                    "model_calls": 1,
                    "tool_calls": 1,
                    "session_state": {"version": 1, "messages": []},
                },
            ]
            for event in events:
                self.wfile.write((json.dumps(event) + "\n").encode())
                self.wfile.flush()
                if event["type"] == "text" and body["input"] == "停止":
                    assert cancelled.wait(5), "Pi never received cancellation"
                    return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    thread.start()
    monkeypatch.setattr(settings, "PI_URL", f"http://127.0.0.1:{server.server_port}")
    try:
        yield cancelled
    finally:
        cancelled.set()
        server.shutdown()
        thread.join(5)
        server.server_close()


def setup(db: Session, client: TestClient, demo: tuple) -> tuple[str, dict, dict]:
    _, people, auth = demo
    org_id = people["E02"].org_id
    enable(db, org_id)
    query = {"org_id": str(org_id)}
    sid = client.post(
        "/api/v1/bot/sessions", params=query, headers=auth["employee"]
    ).json()["session_id"]
    return f"/api/v1/bot/sessions/{sid}", query, auth


def test_ordered_events_and_original_response(
    db: Session,
    client: TestClient,
    demo: tuple,
    fake_pi: Event,
) -> None:
    url, query, auth = setup(db, client, demo)
    for headers in (auth["manager"], auth["foreign"]):
        assert (
            client.post(
                url + "/turns", params=query, headers=headers, json={"text": "你好"}
            ).status_code
            == 404
        )
    assert (
        client.post(
            f"/api/v1/bot/sessions/{uuid4()}/turns",
            params=query,
            headers=auth["employee"],
            json={"text": "你好"},
        ).status_code
        == 404
    )
    response = client.post(
        url + "/turns", params=query, headers=auth["employee"], json={"text": "你好"}
    )
    events = [json.loads(line) for line in response.text.splitlines()]
    assert [e["type"] for e in events] == [
        "text",
        "tool",
        "tool",
        "text",
        "text",
        "final",
    ]
    assert events[1:3] == [
        {
            "type": "tool",
            "name": tools.TITLES["get_my_profile"],
            "phase": phase,
            "ok": True,
        }
        for phase in ("start", "end")
    ]
    expected = {
        "messages": [
            {"type": "text", "text": "第一段第二段第三段", "review_link": None}
        ],
        "steps": [{"name": tools.TITLES["get_my_profile"], "ok": True}],
    }
    assert final_response(response) == expected
    assert (
        client.get(url + "/messages", params=query, headers=auth["employee"]).json()[
            -1
        ]["payload"]
        == expected
    )
    assert not fake_pi.is_set()
    assert set(tools.TITLES) == {t.name for t in tools.definitions()}
    assert all(
        any("\u4e00" <= c <= "\u9fff" for c in title) for title in tools.TITLES.values()
    )


@pytest.mark.parametrize("spec", ["2.3", "2.4"])
def test_disconnect_cancels_joins_rolls_back_and_releases(
    db: Session,
    client: TestClient,
    demo: tuple,
    fake_pi: Event,
    monkeypatch: pytest.MonkeyPatch,
    spec: str,
) -> None:
    url, query, auth = setup(db, client, demo)
    finished = Event()
    original = core.turn

    def tracked(*args: Any, **kwargs: Any) -> Any:
        try:
            return original(*args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(core, "turn", tracked)

    async def application(scope: Any, receive: Any, send: Any) -> None:
        scope["asgi"]["spec_version"] = spec
        await app(scope, receive, send)

    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 18307))
        server = uvicorn.Server(
            uvicorn.Config(application, lifespan="off", log_level="critical")
        )
        thread = Thread(target=server.run, kwargs={"sockets": [listener]})
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started and time.monotonic() < deadline:
                time.sleep(0.01)
            assert server.started
            with httpx.Client(
                base_url="http://127.0.0.1:18307", trust_env=False
            ) as http:
                with http.stream(
                    "POST",
                    url + "/turns",
                    params=query,
                    headers=auth["employee"],
                    json={"text": "停止"},
                ) as response:
                    assert json.loads(next(response.iter_lines())) == {
                        "type": "text",
                        "text": "第一段",
                    }
                assert fake_pi.wait(5)
                assert finished.wait(5), "turn worker survived disconnect"
                with Session(db.get_bind()) as check:
                    p = principal_for(check, demo[1]["E02"].org_id)
                    row = store.get_session(check, p, UUID(url.rsplit("/", 1)[1]))
                    assert row.turn_count == 0 and row.lease_until is None
                    assert row.state_ciphertext is None
                    assert store.messages_for(check, row) == []
                assert (
                    final_response(
                        http.post(
                            url + "/turns",
                            params=query,
                            headers=auth["employee"],
                            json={"text": "继续"},
                        )
                    )["messages"][0]["text"]
                    == "第一段第二段第三段"
                )
        finally:
            fake_pi.set()
            server.should_exit = True
            thread.join(10)
            assert not thread.is_alive()


def test_cancel_after_done_before_save(
    db: Session, demo: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, people, _ = demo
    p = principal_for(db, people["E02"].org_id)
    enable(db, p.org_id)
    row = store.create_session(db, p)
    stream = pi_client.TurnStream(
        str(uuid4()), lambda _: None, lambda *_: None, Event()
    )

    def done(*_args: Any, **_kwargs: Any) -> pi_client.TurnResult:
        stream.cancelled.set()
        return pi_client.TurnResult(
            text="不保存", stop_reason="stop", model_calls=1, tool_calls=0
        )

    monkeypatch.setattr(pi_client, "run_turn", done)
    assert core.turn(db, p, row.id, "取消", stream=stream).messages == []
    db.refresh(row)
    assert row.turn_count == 0 and row.lease_until is None
    assert row.state_ciphertext is None and store.messages_for(db, row) == []


def test_stream_error_finishes_with_original_fallback(
    db: Session,
    client: TestClient,
    demo: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url, query, auth = setup(db, client, demo)

    def fail(*_args: Any, **kwargs: Any) -> Any:
        kwargs["stream"].on_text("未完成的文字")
        raise ModelUnavailable()

    monkeypatch.setattr(pi_client, "run_turn", fail)
    response = client.post(
        url + "/turns", params=query, headers=auth["employee"], json={"text": "失败"}
    )
    result = final_response(response)
    assert result["messages"][0]["text"] == "模型服务暂时不可用，请稍后再试"
    history = client.get(
        url + "/messages", params=query, headers=auth["employee"]
    ).json()
    assert len(history) == 2 and history[0]["payload"] == {"text": "失败"}
    assert history[1]["payload"] == result


def test_cancel_checked_between_pi_events(fake_pi: Event) -> None:
    stream = pi_client.TurnStream(
        str(uuid4()),
        lambda _: None,
        lambda *_: pytest.fail("cancelled tool ran"),
        Event(),
    )
    stream.on_text = lambda _: stream.cancelled.set()
    config = pi_client.PiModelConfig(
        provider="synthetic",
        api="openai-completions",
        id="synthetic",
        baseUrl="http://synthetic",
    )
    with pytest.raises(pi_client.TurnCancelled):
        pi_client.run_turn(
            config,
            mode="chat",
            system_prompt="",
            input="合成",
            tools=tools.definitions(),
            on_tool_call=lambda *_: None,
            stream=stream,
        )
    assert fake_pi.is_set()
    # A cancellation before Pi registration must also avoid running a turn.
    with pytest.raises(pi_client.TurnCancelled):
        pi_client.run_turn(
            config,
            system_prompt="",
            input="合成",
            tools=[],
            on_tool_call=lambda *_: None,
            stream=stream,
        )


@pytest.mark.parametrize("saved_user", [False, True])
@pytest.mark.parametrize("streaming", [False, True])
def test_hourly_limit_counts_active_turn_once(
    db: Session,
    demo: tuple,
    monkeypatch: pytest.MonkeyPatch,
    saved_user: bool,
    streaming: bool,
) -> None:
    p = principal_for(db, demo[1]["E02"].org_id)
    active = store.create_session(db, p)
    target = store.create_session(db, p)
    active.lease_until = utc_now() + timedelta(seconds=admission.LEASE_SECONDS)
    if saved_user:
        store.append_message(db, active, "user", {"text": "已保存的轮次"})
    db.add(active)
    db.commit()
    cancelled = Event() if streaming else None
    monkeypatch.setattr(admission, "HOURLY_TURNS", 1)
    with pytest.raises(admission.BotRateLimited):
        with admission.begin_turn(db, p, target, {"text": "超限"}, cancelled):
            pytest.fail("uncommitted streaming turn was not counted")
    if saved_user:
        monkeypatch.setattr(admission, "HOURLY_TURNS", 2)
        with admission.begin_turn(db, p, target, {"text": "第二轮"}, cancelled):
            pass


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("outcome", ["success", "fallback", "exception"])
def test_tool_steps_survive_history(
    db: Session,
    client: TestClient,
    demo: tuple,
    monkeypatch: pytest.MonkeyPatch,
    streaming: bool,
    outcome: str,
) -> None:
    url, query, auth = setup(db, client, demo)
    original = tools.call

    def call(*args: Any, **kwargs: Any) -> Any:
        if outcome == "exception" and args[-1]:
            raise ModelUnavailable()
        return original(*args, **kwargs)

    def run(*_args: Any, **kwargs: Any) -> pi_client.TurnResult:
        kwargs["on_tool_call"]("get_my_profile", {})
        kwargs["on_tool_call"]("get_my_profile", {"unexpected": "private-argument"})
        if outcome == "fallback":
            raise ModelUnavailable()
        return pi_client.TurnResult(
            text="完成", stop_reason="stop", model_calls=1, tool_calls=2
        )

    monkeypatch.setattr(tools, "call", call)
    monkeypatch.setattr(pi_client, "run_turn", run)
    if streaming:
        response = client.post(
            url + "/turns",
            params=query,
            headers=auth["employee"],
            json={"text": "步骤"},
        )
        result = final_response(response)
        ends = [
            {"name": event["name"], "ok": event["ok"]}
            for line in response.text.splitlines()
            if (event := json.loads(line))["type"] == "tool" and event["phase"] == "end"
        ]
        assert ends == result["steps"]
        assert "private-argument" not in response.text
    else:
        p = principal_for(db, demo[1]["E02"].org_id)
        result = core.turn(db, p, UUID(url.rsplit("/", 1)[1]), "步骤").model_dump(
            mode="json"
        )
    assert result["steps"] == [
        {"name": tools.TITLES["get_my_profile"], "ok": ok} for ok in (True, False)
    ]
    history = client.get(
        url + "/messages", params=query, headers=auth["employee"]
    ).json()
    assert history[-1]["payload"] == result
