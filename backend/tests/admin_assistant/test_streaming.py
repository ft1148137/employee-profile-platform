import json
import socket
import time
from threading import Event, Thread
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.domains.admin_assistant import core, registry, store, tools
from app.domains.admin_assistant.models import AdminChangeSet
from app.domains.admin_assistant.schemas import AdminTurnInput
from app.domains.bot import store as messages
from app.domains.model import pi_client
from app.domains.model.client import ModelUnavailable
from app.main import app
from tests.api.routes.test_bot_streaming import fake_pi as fake_pi
from tests.domains.test_admin_assistant import admin
from tests.domains.test_bot_core import enable
from tests.utils.bot import final_response

pytestmark = pytest.mark.xdist_group("model")


def setup(db: Session, client: TestClient, demo: tuple) -> tuple[str, dict, dict]:
    org_id = demo[1]["E02"].org_id
    enable(db, org_id)
    query = {"org_id": str(org_id)}
    sid = client.post(
        "/api/v1/admin-assistant/sessions", params=query, headers=demo[2]["admin"]
    ).json()["session_id"]
    return f"/api/v1/admin-assistant/sessions/{sid}", query, demo[2]


def test_ordered_events_and_original_response(
    db: Session,
    client: TestClient,
    demo: tuple,
    fake_pi: Event,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url, query, auth = setup(db, client, demo)
    now = registry.utc_now()
    monkeypatch.setattr(registry, "utc_now", lambda: now)
    body = {"text": "你好", "page": "/admin"}
    response = client.post(
        url + "/turns", params=query, headers=auth["admin"], json=body
    )
    events = [json.loads(line) for line in response.text.splitlines()]
    assert [event["type"] for event in events] == [
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
            "name": registry.FUNCTIONS["list_roles"].description,
            "phase": phase,
            "ok": True,
        }
        for phase in ("start", "end")
    ]
    p = admin(db, demo[1]["E02"].org_id)
    original = core.turn(db, p, UUID(url.rsplit("/", 1)[1]), AdminTurnInput(**body))
    assert final_response(response) == original.model_dump(mode="json")
    assert client.get(url + "/messages", params=query, headers=auth["admin"]).json()[
        -1
    ]["payload"] == final_response(response)
    for role in ("employee", "manager", "foreign"):
        denied = client.post(
            url + "/turns", params=query, headers=auth[role], json=body
        )
        assert (
            denied.status_code == 404
            and denied.headers["content-type"] == "application/json"
        )
    assert (
        client.post(
            f"/api/v1/admin-assistant/sessions/{uuid4()}/turns",
            params=query,
            headers=auth["admin"],
            json=body,
        ).status_code
        == 404
    )
    assert not fake_pi.is_set()


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
        listener.bind(("127.0.0.1", 18321))
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
                base_url="http://127.0.0.1:18321", trust_env=False
            ) as http:
                with http.stream(
                    "POST",
                    url + "/turns",
                    params=query,
                    headers=auth["admin"],
                    json={"text": "停止", "page": "/admin"},
                ) as response:
                    lines = response.iter_lines()
                    assert json.loads(next(lines))["type"] == "tool"
                    assert json.loads(next(lines))["ok"] is True
                    assert json.loads(next(lines)) == {
                        "type": "text",
                        "text": "第一段",
                    }
                assert fake_pi.wait(5)
                assert finished.wait(5), "turn worker survived disconnect"
                with Session(db.get_bind()) as check:
                    p = admin(check, demo[1]["E02"].org_id)
                    row = store.get_session(check, p, UUID(url.rsplit("/", 1)[1]))
                    assert row.turn_count == 0 and row.lease_until is None
                    assert not check.exec(
                        select(AdminChangeSet).where(
                            AdminChangeSet.session_id == row.id
                        )
                    ).all()
                    assert row.state_ciphertext is None
                    assert messages.messages_for(check, row) == []
                assert (
                    final_response(
                        http.post(
                            url + "/turns",
                            params=query,
                            headers=auth["admin"],
                            json={"text": "继续", "page": "/admin"},
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
    p = admin(db, people["E02"].org_id)
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
    assert (
        core.turn(
            db, p, row.id, AdminTurnInput(text="取消", page="/admin"), stream=stream
        ).messages
        == []
    )
    db.refresh(row)
    assert row.turn_count == 0 and row.lease_until is None
    assert row.state_ciphertext is None and messages.messages_for(db, row) == []


@pytest.mark.parametrize(
    "name,args,expected",
    [
        ("admin_read", {"function": "missing"}, "读取信息"),
        ("propose_changes", {"items": []}, "起草变更"),
        (
            "propose_changes",
            {"items": [{"function": "update_update_settings"}]},
            registry.FUNCTIONS["update_update_settings"].description,
        ),
    ],
)
def test_safe_tool_titles(name: str, args: dict, expected: str) -> None:
    assert tools.title(name, args) == expected


def test_stream_failure_keeps_original_fallback_and_safe_progress(
    db: Session, client: TestClient, demo: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    url, query, auth = setup(db, client, demo)

    def fail(*_args: Any, **kwargs: Any) -> Any:
        kwargs["stream"].on_text("未完成的文字")
        kwargs["on_tool_call"](
            "admin_read",
            {"function": "missing", "args": {"secret": "synthetic-private-argument"}},
        )
        raise ModelUnavailable()

    monkeypatch.setattr(pi_client, "run_turn", fail)
    response = client.post(
        url + "/turns",
        params=query,
        headers=auth["admin"],
        json={"text": "失败", "page": "/admin"},
    )
    result = final_response(response)
    assert (
        result["messages"][0]["text"]
        == "模型暂时不可用，请稍后重试，也可以继续使用手工页面。"
    )
    events = [json.loads(line) for line in response.text.splitlines()]
    assert events[2] == {
        "type": "tool",
        "name": "读取信息",
        "phase": "end",
        "ok": False,
    }
    assert "synthetic-private-argument" not in response.text
    history = client.get(url + "/messages", params=query, headers=auth["admin"]).json()
    assert len(history) == 2 and history[0]["payload"]["text"] == "失败"
    assert history[1]["payload"] == result
