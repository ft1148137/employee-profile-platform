"""Controlled failures through the original evaluation and bot/Pi callback path."""

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from pydantic import ValidationError
from sqlmodel import Session

from app.domains.bot import core, tools
from app.domains.model import pi_client
from app.domains.model.client import ModelUnavailable

CASES = Path(__file__).parent / "planning/cases"


@pytest.mark.parametrize("case", ["direct-01", "newcomer-yes-01", "delay-01"])
@pytest.mark.parametrize("mode", ["agent", "agent_validator"])
def test_scripted_samples(db: Session, case: str, mode: str) -> None:
    from tests.eval.coordination.test_coordination_eval import evaluate

    result = evaluate(
        db,
        yaml.safe_load((CASES / f"{case}.yaml").read_text()),
        mode,
        True,
        "synthetic",
        "off",
    )
    assert result["status"] == "passed"
    assert result["parse_failure"] is False
    if case == "newcomer-yes-01":
        assert result["newcomer_baseline"] is True
        if mode == "agent_validator":
            assert result["newcomer"] is True


@pytest.mark.parametrize(
    ("failure", "case", "origin"),
    [
        ("parameters", "direct-01", "ValidationError"),
        ("permission", "newcomer-yes-01", "CapacityNotFound"),
        ("service", "delay-01", "RuntimeError"),
        ("provider", "direct-01", "ModelServiceError"),
        ("protocol", "direct-01", "ModelUnavailable"),
        ("output", "delay-01", None),
    ],
)
def test_classified_failure_path(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    case: str,
    origin: str | None,
) -> None:
    from tests.eval.coordination.test_coordination_eval import evaluate

    observed: dict[str, Any] = {}
    original_client = httpx.Client
    original_turn = pi_client.run_turn
    tool = tools.COORDINATION["simulate_plan"]
    original_validate = tool.input_model.model_validate

    def validate(value: Any, **kwargs: Any) -> Any:
        try:
            return original_validate(value, **kwargs)
        except ValidationError as error:
            observed["origin"] = type(error).__name__
            observed["validation"] = [
                (entry["loc"], entry["type"])
                for entry in error.errors(include_input=False)
            ]
            raise

    def handler(*args: Any) -> Any:
        try:
            if failure == "service":
                raise RuntimeError("synthetic service failure")
            if failure == "permission":
                args = (replace(args[0], managed_department_ids=frozenset()), *args[1:])
            return tool.handler(*args)
        except Exception as error:
            observed["origin"] = type(error).__name__
            raise

    def traced_turn(config: Any, **kwargs: Any) -> pi_client.TurnResult:
        callback = kwargs["on_tool_call"]

        def call(name: str, arguments: dict) -> Any:
            try:
                return callback(name, arguments)
            except ModelUnavailable as error:
                observed["callback"] = (type(error).__name__, error.code)
                raise

        kwargs["on_tool_call"] = call
        try:
            return original_turn(config, **kwargs)
        except ModelUnavailable as error:
            observed["pi"] = (type(error).__name__, error.code)
            if failure in {"provider", "protocol"}:
                observed["origin"] = type(error).__name__
            raise

    def transport(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/v1/turns":
            if "/results/" in request.url.path:
                observed["posted"] = json.loads(request.content)
            return httpx.Response(200, json={})
        body = json.loads(request.content)
        if failure == "protocol":
            return httpx.Response(200, content=b"invalid-json\n")
        events: list[dict] = []
        if failure == "provider":
            events.append({"type": "error", "code": "upstream_unavailable"})
        elif failure == "output":
            events.extend(
                [
                    {"type": "text", "text": "没有最终方案"},
                    {
                        "type": "done",
                        "stopReason": "stop",
                        "model_calls": 1,
                        "tool_calls": 0,
                        "session_state": {"version": 1, "messages": []},
                    },
                ]
            )
        else:
            demand_id = re.search(r"需求编号 ([0-9a-f-]{36})", body["input"])
            assert demand_id is not None
            arguments = {} if failure == "parameters" else {"demand_id": demand_id[1]}
            observed["shape"] = dict.fromkeys(arguments, "uuid")
            events.append(
                {
                    "type": "tool_call",
                    "call_id": "synthetic-call",
                    "name": "coordination",
                    "arguments": {"function": "simulate_plan", "arguments": arguments},
                }
            )
        return httpx.Response(
            200, content="".join(json.dumps(e) + "\n" for e in events)
        )

    monkeypatch.setattr(tool.input_model, "model_validate", validate)
    monkeypatch.setitem(tools.COORDINATION, tool.name, replace(tool, handler=handler))
    monkeypatch.setattr(pi_client, "run_turn", traced_turn)
    monkeypatch.setattr(
        pi_client.httpx,
        "Client",
        lambda **kwargs: original_client(
            **kwargs, transport=httpx.MockTransport(transport)
        ),
    )
    monkeypatch.setattr(
        core,
        "model_config",
        lambda _: pi_client.PiModelConfig(
            provider="synthetic",
            api="openai-completions",
            id="synthetic",
            baseUrl="http://synthetic.invalid",
        ),
    )
    result = evaluate(
        db,
        yaml.safe_load((CASES / f"{case}.yaml").read_text()),
        "agent" if failure == "output" else "agent_validator",
        False,
        "synthetic",
        "off",
    )
    assert observed.get("origin") == origin
    assert result["parse_failure"] is True
    if failure == "output":
        assert result["status"] == "passed"
        assert result["model_calls"] == 1
        assert "pi" not in observed
    else:
        assert result["status"] == "failed:model_unavailable"
        assert result["model_calls"] is None  # No successful TurnResult was obtained.
    if failure in {"parameters", "permission", "service"}:
        if failure == "service":
            assert observed["callback"] == ("ModelUnavailable", "coordination_tool")
            assert observed["posted"] == {
                "result": "平台工具调用失败",
                "is_error": True,
            }
        else:
            assert "callback" not in observed
            assert observed["posted"] == {
                "result": {
                    "error": "工具参数不正确"
                    if failure == "parameters"
                    else "未找到该需求，或你无权查看（仅发起人及双方部门经理可见）。"
                },
                "is_error": False,
            }
        assert observed["pi"] == ("ModelUnavailable", "unavailable")
        assert observed["shape"] == (
            {} if failure == "parameters" else {"demand_id": "uuid"}
        )
    if failure == "parameters":
        assert observed["validation"] == [(("demand_id",), "missing")]
