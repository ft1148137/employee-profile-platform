import json
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import JsonValue

from app.domains.model import pi_client
from app.domains.model.client import ModelRateLimited, ModelUnavailable
from app.domains.model.pi_client import HistoryMessage, PiBusy, ToolDefinition, run_turn
from tests.utils.pi import PiHarness

TOOLS = [
    ToolDefinition(
        name="submit_draft",
        description="提交候选",
        parameters={
            "type": "object",
            "additionalProperties": False,
            "properties": {"statement": {"type": "string"}},
            "required": ["statement"],
        },
    )
]


def test_chat_roundtrip_with_history(pi_service: PiHarness) -> None:
    result = run_turn(
        pi_service.config,
        mode="chat",
        history=[
            HistoryMessage(role="user", text="合成旧问题"),
            HistoryMessage(role="assistant", text="合成旧回答"),
        ],
        system_prompt="只用平台工具",
        input="合成新输入",
        tools=TOOLS,
        on_tool_call=lambda *_: {"accepted": True},
    )
    assert result.text == "已完成"
    messages = pi_service.requests[0]["body"]["messages"]
    transcript = json.dumps(messages, ensure_ascii=False)
    assert "合成旧问题" in transcript and "合成旧回答" in transcript
    assert any(
        m["role"] == "assistant" and m["content"] == "合成旧回答" for m in messages
    )


def test_chat_busy_has_typed_error_and_shorter_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = httpx.Client
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(503, json={"type": "error", "code": "busy"})

    monkeypatch.setattr(
        pi_client.httpx,
        "Client",
        lambda **kwargs: real(**kwargs, transport=httpx.MockTransport(handler)),
    )
    config = pi_client.PiModelConfig(
        provider="test", api="openai-completions", id="test", baseUrl="http://test"
    )
    with pytest.raises(PiBusy):
        run_turn(
            config,
            mode="chat",
            history=[],
            system_prompt="",
            input="",
            tools=[],
            on_tool_call=lambda *_: None,
        )
    assert seen[0].extensions["timeout"]["read"] == 70
    assert json.loads(seen[0].content)["history"] == []
    with pytest.raises(ValueError):
        run_turn(
            config,
            history=[],
            system_prompt="",
            input="",
            tools=[],
            on_tool_call=lambda *_: None,
        )
    with pytest.raises(ValueError):
        run_turn(
            config,
            mode="chat",
            history=[HistoryMessage(role="user", text="x")] * 21,
            system_prompt="",
            input="",
            tools=[],
            on_tool_call=lambda *_: None,
        )
    assert len(seen) == 1


def test_real_pi_round_trip(pi_service: PiHarness) -> None:
    seen = []

    def tool(name: str, args: dict[str, JsonValue]) -> JsonValue:
        seen.append((name, args))
        return {"accepted": True}

    result = run_turn(
        pi_service.config,
        system_prompt="只用工具",
        input="合成证据",
        tools=TOOLS,
        on_tool_call=tool,
    )
    assert seen == [("submit_draft", {"statement": "合成建议"})]
    assert result.text == "已完成"
    assert result.stop_reason == "stop"
    assert result.model_calls == 2 and result.tool_calls == 1
    assert len(pi_service.requests) == 2
    assert any(
        m.get("role") == "tool" and json.loads(m["content"]) == {"accepted": True}
        for m in pi_service.requests[-1]["body"]["messages"]
    )
    for secret in [
        "synthetic-api-key",
        "synthetic-service-token",
        "合成证据",
        "Authorization",
    ]:
        assert secret not in pi_service.logs.read_text()


def test_callback_exception_cancels_and_redacts(
    pi_service: PiHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    responses = []
    real = httpx.Client
    monkeypatch.setattr(
        pi_client.httpx,
        "Client",
        lambda **kwargs: real(**kwargs, event_hooks={"response": [responses.append]}),
    )

    def fail(_name: str, _args: dict[str, JsonValue]) -> JsonValue:
        raise RuntimeError("sensitive callback exception")

    with pytest.raises(ModelUnavailable, match="平台工具调用失败"):
        run_turn(
            pi_service.config,
            system_prompt="",
            input="合成证据",
            tools=TOOLS,
            on_tool_call=fail,
        )
    logs = pi_service.logs.read_text()
    assert any(r.request.method == "DELETE" and r.status_code == 404 for r in responses)
    assert len(pi_service.requests) == 1
    assert "sensitive" not in logs


def test_quota_response_preserves_retry_time(pi_service: PiHarness) -> None:
    pi_service.rate_limited = True
    before = datetime.now(UTC)
    with pytest.raises(ModelRateLimited) as error:
        run_turn(
            pi_service.config,
            system_prompt="",
            input="",
            tools=TOOLS,
            on_tool_call=lambda *_: None,
        )
    assert error.value.retry_at is not None
    assert 59 <= (error.value.retry_at - before).total_seconds() <= 65
    assert "sensitive" not in str(error.value)
    assert len(pi_service.requests) == 1


@pytest.mark.parametrize(
    "code,message",
    [
        ("busy", "模型服务繁忙，请稍后再试"),
        ("deadline", "模型调用超时"),
        ("limit_exceeded", "模型调用次数达到上限"),
        ("cancelled", "模型调用已取消"),
        ("model_error", "模型服务暂时不可用"),
        ("unauthorized", "模型服务暂时不可用"),
        ("bad_request", "模型服务暂时不可用"),
        ("model_unauthorized", "密钥不对或没有权限"),
        ("model_not_found", "这个模型不可用，请换一个模型"),
        ("upstream_rejected", "模型服务拒绝了请求，请检查模型设置"),
        ("upstream_unavailable", "模型服务暂时不可用，请稍后重试"),
        ("unknown-secret-code", "模型服务暂时不可用"),
    ],
)
def test_protocol_error_mapping(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    code: str,
    message: str,
) -> None:
    real = httpx.Client

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503, json={"type": "error", "code": code, "raw": "sensitive"}
        )

    monkeypatch.setattr(
        pi_client.httpx,
        "Client",
        lambda **kwargs: real(**kwargs, transport=httpx.MockTransport(handler)),
    )
    from app.domains.model.pi_client import PiModelConfig

    with pytest.raises(ModelUnavailable) as error:
        run_turn(
            PiModelConfig(
                provider="test",
                api="openai-completions",
                id="test",
                baseUrl="http://test",
            ),
            system_prompt="",
            input="",
            tools=[],
            on_tool_call=lambda *_: None,
        )
    assert "sensitive" not in str(error.value) + caplog.text
    assert str(error.value) == message
    assert ("Pi protocol failure" in caplog.text) is (
        code in ("unauthorized", "bad_request")
    )


@pytest.mark.parametrize(
    "payload",
    [
        b'{"type":"wrong"}\n',
        b"invalid\n",
        b"",
        b'{"type":"error","code":"rate_limited","retry_at":"invalid"}\n',
    ],
)
def test_invalid_or_truncated_stream_is_cancelled(
    monkeypatch: pytest.MonkeyPatch, payload: bytes
) -> None:
    real = httpx.Client
    methods = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        return httpx.Response(
            200, content=payload if request.method == "POST" else b"{}"
        )

    monkeypatch.setattr(
        pi_client.httpx,
        "Client",
        lambda **kwargs: real(**kwargs, transport=httpx.MockTransport(handler)),
    )
    from app.domains.model.pi_client import PiModelConfig

    with pytest.raises(ModelUnavailable):
        run_turn(
            PiModelConfig(
                provider="test",
                api="openai-completions",
                id="test",
                baseUrl="http://test",
            ),
            system_prompt="",
            input="",
            tools=[],
            on_tool_call=lambda *_: None,
        )
    # A terminal error event needs no DELETE; malformed/truncated streams do.
    assert methods == (["POST"] if b'"type":"error"' in payload else ["POST", "DELETE"])


def test_unreachable_service_is_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    real = httpx.Client

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sensitive connection details")

    monkeypatch.setattr(
        pi_client.httpx,
        "Client",
        lambda **kwargs: real(**kwargs, transport=httpx.MockTransport(handler)),
    )
    from app.domains.model.pi_client import PiModelConfig

    with pytest.raises(ModelUnavailable, match="模型服务连接失败或协议错误"):
        run_turn(
            PiModelConfig(
                provider="test",
                api="openai-completions",
                id="test",
                baseUrl="http://test",
            ),
            system_prompt="",
            input="",
            tools=[],
            on_tool_call=lambda *_: None,
        )


@pytest.mark.parametrize("interrupted", [False, True])
def test_callback_interrupt_and_finish_race(
    monkeypatch: pytest.MonkeyPatch, interrupted: bool
) -> None:
    real = httpx.Client
    methods = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.method == "DELETE":
            return httpx.Response(200 if interrupted else 404, json={})
        payload = b'{"type":"tool_call","call_id":"1","name":"submit_draft","arguments":{}}\n{"type":"done"}\n'
        return httpx.Response(
            200, content=payload if request.url.path == "/v1/turns" else b"{}"
        )

    monkeypatch.setattr(
        pi_client.httpx,
        "Client",
        lambda **kwargs: real(**kwargs, transport=httpx.MockTransport(handler)),
    )

    def fail(*_args: object) -> JsonValue:
        if interrupted:
            raise KeyboardInterrupt()
        raise RuntimeError("sensitive")

    from app.domains.model.pi_client import PiModelConfig

    with pytest.raises(KeyboardInterrupt if interrupted else ModelUnavailable):
        run_turn(
            PiModelConfig(
                provider="test",
                api="openai-completions",
                id="test",
                baseUrl="http://test",
            ),
            system_prompt="",
            input="",
            tools=TOOLS,
            on_tool_call=fail,
        )
    assert methods == (
        ["POST", "DELETE"] if interrupted else ["POST", "POST", "DELETE"]
    )
