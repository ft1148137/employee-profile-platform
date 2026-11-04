import json
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from app.domains.model.client import ModelUnavailable
from app.domains.model.models import ModelSetting
from app.domains.model.pi_client import run_turn
from app.domains.model.schemas import CLOUD_PROVIDERS, ModelSettingUpdate
from app.domains.model.service import model_config, validate_capabilities
from tests.utils.pi import PiHarness


@pytest.mark.parametrize(
    "provider,model,effort,path,field",
    [
        ("openai", "gpt-5", "high", "/v1/responses", "reasoning"),
        ("anthropic", "claude-3-7-sonnet-20250219", "high", "/v1/messages", "thinking"),
        ("deepseek", "deepseek-v4-flash", "high", "/v1/chat/completions", "thinking"),
        ("qwen", "synthetic", "high", "/v1/chat/completions", "reasoning_effort"),
        ("moonshotai", "kimi-k2-thinking", "off", "/v1/chat/completions", None),
        ("moonshotai-cn", "kimi-k2-thinking", "off", "/v1/chat/completions", None),
        ("kimi-coding", "kimi-for-coding", "high", "/v1/messages", "thinking"),
        (
            "openrouter",
            "alibaba/tongyi-deepresearch-30b-a3b",
            "high",
            "/v1/chat/completions",
            "reasoning",
        ),
        (
            "openai-compatible",
            "synthetic",
            "xhigh",
            "/v1/chat/completions",
            "reasoning_effort",
        ),
        ("ollama", "synthetic", "high", "/v1/chat/completions", "reasoning_effort"),
    ],
)
def test_provider_transport(
    provider: str,
    model: str,
    effort: str,
    path: str,
    field: str | None,
    pi_service: PiHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if provider in CLOUD_PROVIDERS:
        monkeypatch.setitem(
            CLOUD_PROVIDERS,
            provider,
            (
                CLOUD_PROVIDERS[provider][0],
                pi_service.config.baseUrl.removesuffix("/v1")
                if provider in ("anthropic", "kimi-coding")
                else pi_service.config.baseUrl,
            ),
        )
    setting = ModelSetting(
        provider=provider,
        model=model,
        enabled=True,
        kind="local"
        if provider == "ollama"
        else "openai_compatible"
        if provider == "openai-compatible"
        else "cloud_api",
        base_url=pi_service.config.baseUrl,
        reasoning_effort=effort,
    )
    validate_capabilities(provider, model, effort)
    config = model_config(setting)
    received = []
    send = httpx.Client.send

    def record(
        client: httpx.Client, request: httpx.Request, **kwargs: Any
    ) -> httpx.Response:
        if request.url.path == "/v1/turns":
            received.append(json.loads(request.content)["model"])
        return send(client, request, **kwargs)

    monkeypatch.setattr(httpx.Client, "send", record)
    pi_service.replies.append({"status": 400})
    with pytest.raises(ModelUnavailable):
        run_turn(
            config,
            system_prompt="合成测试",
            input="测试",
            tools=[],
            on_tool_call=lambda *_: None,
        )
    assert received[0]["provider"] == provider
    assert received[0]["thinkingLevel"] == effort
    if provider not in ("qwen", "openai-compatible", "ollama"):
        assert not set(received[0]) & {
            "reasoning",
            "contextWindow",
            "maxTokens",
            "compat",
        }
    else:
        assert received[0]["reasoning"] == (effort != "off")
    request = pi_service.requests[0]
    assert request["path"] == path and request["body"]["model"] == model
    assert (
        "authorization" not in request["headers"]
        and "x-api-key" not in request["headers"]
    )
    if field:
        assert field in request["body"]
    else:
        assert "reasoning_effort" not in request["body"]


def test_capability_errors(
    pi_service: PiHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert pi_service.config.provider == "openai-compatible"
    with pytest.raises(ModelUnavailable, match="推理强度 high 不适用于该模型"):
        validate_capabilities("openai", "gpt-4o-mini", "high")
    with pytest.raises(ModelUnavailable, match="不在支持的列表"):
        validate_capabilities("openai", "not-in-catalog", None)
    from app.core.config import settings

    monkeypatch.setattr(settings, "PI_URL", "http://127.0.0.1:1")
    with pytest.raises(ModelUnavailable, match="稍后再试"):
        validate_capabilities("openai", "gpt-4o-mini", None)


@pytest.mark.parametrize(
    "url",
    ["https://example.com/v1", "http://169.254.169.254", "http://[fd00:ec2::254]"],
)
def test_cloud_address_is_fixed(url: str) -> None:
    with pytest.raises(ValidationError, match="固定地址"):
        ModelSettingUpdate(
            enabled=True,
            kind="cloud_api",
            provider="openai",
            model="gpt-4o-mini",
            base_url=url,
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254",
        "http://[fd00:ec2::254]",
        "http://[fe80::1]",
        "http://[::ffff:169.254.169.254]",
    ],
)
def test_custom_metadata_url_is_rejected(url: str) -> None:
    with pytest.raises(ValidationError, match="元数据"):
        ModelSettingUpdate(
            enabled=True,
            kind="openai_compatible",
            provider="openai-compatible",
            model="synthetic",
            base_url=url,
        )


def test_production_compose_has_no_loopback_override() -> None:
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    rendered = subprocess.run(
        [
            "docker",
            "compose",
            "-p",
            "nere102",
            "--env-file",
            ".env.example",
            "-f",
            "compose.yml",
            "config",
            "--format",
            "json",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    config = json.loads(rendered.stdout)
    assert "PI_EGRESS_ALLOW_LOOPBACK" not in config["services"]["pi"]["environment"]
