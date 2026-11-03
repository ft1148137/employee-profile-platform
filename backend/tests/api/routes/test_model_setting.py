import subprocess
import sys
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.api.routes import model_setting as route
from app.core.config import settings
from app.core.secrets import SecretUnavailable, decrypt_secret, encrypt_secret
from app.domains.audit.models import AuditEvent
from app.domains.model import client as model_client
from app.domains.model.client import (
    ModelConfig,
    ModelOutputInvalid,
    ModelRateLimited,
    ModelUnavailable,
    _retry_at,
    _sdk,
    chat_json,
)
from app.domains.model.models import ModelSetting
from tests.authz.test_tenant_access import headers, tenant_data  # noqa: F401

CLOUD_SETTING = {
    "enabled": True,
    "kind": "cloud_api",
    "model": "openai/gpt-4o-mini",
    "api_key": "synthetic-key",
}


@pytest.fixture
def completions(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[Any], list[dict[str, Any]]]:
    sdk = _sdk()
    real = sdk.completion
    replies: list[Any] = []
    calls: list[dict[str, Any]] = []

    def completion(**kwargs: Any) -> Any:
        calls.append({**kwargs, "messages": list(kwargs["messages"])})
        return real(**kwargs, mock_response=replies.pop(0))

    monkeypatch.setattr(sdk, "completion", completion)
    monkeypatch.setattr(model_client.time, "sleep", lambda _: None)
    return replies, calls


def test_encryption(monkeypatch: pytest.MonkeyPatch) -> None:
    a, b = encrypt_secret("synthetic-key"), encrypt_secret("synthetic-key")
    assert a != b and decrypt_secret(a) == "synthetic-key"
    monkeypatch.setattr(settings, "SECRET_KEY", "another-test-key")
    with pytest.raises(SecretUnavailable, match="保存的密钥已失效"):
        decrypt_secret(a)
    with pytest.raises(SecretUnavailable):
        decrypt_secret("invalid")


def test_settings(
    request: pytest.FixtureRequest, client: TestClient, db: Session
) -> None:
    orgs, _, _, users = request.getfixturevalue("tenant_data")
    url = f"/api/v1/orgs/{orgs[0].id}/model-setting"
    auth = headers(users[0])
    assert (
        client.get(f"/api/v1/orgs/{orgs[1].id}/model-setting", headers=auth).status_code
        == 404
    )
    assert client.get(url, headers=auth).json() == {
        "enabled": False,
        "kind": None,
        "model": None,
        "base_url": None,
        "has_api_key": False,
        "updated_at": None,
    }
    body = {
        **CLOUD_SETTING,
        "base_url": "https://example.com/v1?query=private",
    }
    result = client.put(url, headers=auth, json=body)
    assert result.status_code == 200 and result.json()["has_api_key"]
    assert "synthetic-key" not in result.text and "ciphertext" not in result.text
    stored = db.get(ModelSetting, orgs[0].id)
    assert stored and decrypt_secret(stored.api_key_ciphertext) == "synthetic-key"
    token = stored.api_key_ciphertext
    body.pop("api_key")
    assert client.put(url, headers=auth, json=body).status_code == 200
    db.refresh(stored)
    assert stored.api_key_ciphertext == token
    event = db.exec(
        select(AuditEvent)
        .where(AuditEvent.org_id == orgs[0].id)
        .order_by(AuditEvent.created_at.desc())
    ).first()
    assert event and event.details == {
        "enabled": True,
        "kind": "cloud_api",
        "model": body["model"],
        "base_url": "example.com",
        "api_key_changed": False,
    }
    assert "synthetic-key" not in str(event.details)
    assert client.get(url, headers=auth).json()["has_api_key"]
    for index, status in ((1, 403), (2, 403), (3, 404)):
        for method, path, kwargs in (
            ("GET", url, {}),
            ("PUT", url, {"json": body}),
            ("POST", url + "/test", {}),
        ):
            assert (
                client.request(
                    method, path, headers=headers(users[index]), **kwargs
                ).status_code
                == status
            )
    for update in ({"api_key": ""}, {"kind": "anthropic", "api_key": "synthetic-key"}):
        assert client.put(url, headers=auth, json={**body, **update}).status_code == 422
    assert (
        client.put(
            url, headers=auth, json={**body, "kind": "local", "api_key": ""}
        ).json()["has_api_key"]
        is False
    )
    assert client.put(url, headers=auth, json={"enabled": False}).status_code == 200


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("key_input", [{}, {"api_key": None}])
@pytest.mark.parametrize(
    "update",
    [
        {"base_url": "https://other.example.com/v1"},
        {"base_url": None},
        {"model": "anthropic/claude"},
        {"kind": "local"},
    ],
)
def test_saved_key_cannot_follow_destination_change(
    enabled: bool,
    key_input: dict[str, Any],
    update: dict[str, Any],
    request: pytest.FixtureRequest,
    client: TestClient,
    db: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    orgs, _, _, users = request.getfixturevalue("tenant_data")
    url = f"/api/v1/orgs/{orgs[0].id}/model-setting"
    auth = headers(users[0])
    body = {**CLOUD_SETTING, "enabled": enabled, "base_url": "https://example.com/v1"}
    assert client.put(url, headers=auth, json=body).status_code == 200
    stored = db.get(ModelSetting, orgs[0].id)
    assert stored
    before = stored.model_dump()
    audit_query = select(AuditEvent).where(AuditEvent.org_id == orgs[0].id)
    audit_ids = [event.id for event in db.exec(audit_query).all()]
    body.pop("api_key")
    response = client.put(url, headers=auth, json={**body, **update, **key_input})
    assert response.status_code == 422
    assert response.json() == {
        "detail": "更换了模型类型、提供方或服务地址，请重新填写密钥"
    }
    db.refresh(stored)
    assert stored.model_dump() == before
    assert [event.id for event in db.exec(audit_query).all()] == audit_ids
    assert "synthetic-key" not in response.text + caplog.text


@pytest.mark.parametrize(
    "initial_key,initial_url,update,expected_key",
    [
        ("synthetic-key", None, {"model": "openai/another"}, "synthetic-key"),
        ("synthetic-key", None, {"base_url": ""}, "synthetic-key"),
        ("synthetic-key", "", {"base_url": None}, "synthetic-key"),
        (
            "synthetic-key",
            "https://example.com/v1/",
            {"base_url": "https://example.com/v1"},
            "synthetic-key",
        ),
        (
            "synthetic-key",
            "https://example.com/v1",
            {"base_url": "https://example.com/v1///"},
            "synthetic-key",
        ),
        (
            "synthetic-key",
            None,
            {
                "base_url": "https://other.example.com/v1",
                "api_key": "synthetic-new-key",
            },
            "synthetic-new-key",
        ),
        (
            "synthetic-key",
            None,
            {
                "kind": "local",
                "model": "ollama_chat/llama3",
                "base_url": "http://localhost:11434",
                "api_key": "",
            },
            None,
        ),
        ("", "http://localhost:11434", {"base_url": "http://localhost:11435"}, None),
    ],
)
def test_saved_key_destination_updates(
    initial_key: str,
    initial_url: str | None,
    update: dict[str, Any],
    expected_key: str | None,
    request: pytest.FixtureRequest,
    client: TestClient,
    db: Session,
) -> None:
    orgs, _, _, users = request.getfixturevalue("tenant_data")
    url = f"/api/v1/orgs/{orgs[0].id}/model-setting"
    auth = headers(users[0])
    body = {**CLOUD_SETTING, "base_url": initial_url, "api_key": initial_key}
    if not initial_key:
        body["kind"] = "local"
    assert client.put(url, headers=auth, json=body).status_code == 200
    stored = db.get(ModelSetting, orgs[0].id)
    assert stored
    old_token = stored.api_key_ciphertext
    body.pop("api_key")
    response = client.put(url, headers=auth, json={**body, **update})
    assert response.status_code == 200
    assert response.json()["has_api_key"] == bool(expected_key)
    db.refresh(stored)
    assert stored.base_url == update.get("base_url", initial_url)
    assert (
        decrypt_secret(stored.api_key_ciphertext) if stored.api_key_ciphertext else None
    ) == expected_key
    if "api_key" not in update:
        assert stored.api_key_ciphertext == old_token


@pytest.mark.parametrize(
    "update",
    [
        {"kind": None},
        {"model": None},
        {"model": "no-prefix"},
        {"model": "/"},
        {"model": "a/"},
        {"model": "x" * 201},
        {"base_url": "x" * 501},
        {"base_url": "ftp://example.com"},
        {"base_url": "https://user:pass@example.com"},
        {"kind": "local", "base_url": None},
        {"kind": "openai_compatible", "base_url": None},
        {"kind": "openai_compatible", "model": "anthropic/claude"},
        {"kind": "chatgpt_subscription"},
        {"model": "chatgpt/gpt-5"},
        {"model": "github_copilot/gpt-5"},
        {"model": "anthropic/claude", "api_key": "sk-ant-oat-test"},
        {"api_key": ["synthetic-key"]},
    ],
)
def test_validation(
    update: dict, request: pytest.FixtureRequest, client: TestClient
) -> None:
    orgs, _, _, users = request.getfixturevalue("tenant_data")
    response = client.put(
        f"/api/v1/orgs/{orgs[0].id}/model-setting",
        headers=headers(users[0]),
        json={**CLOUD_SETTING, **update},
    )
    assert response.status_code == 422
    assert (
        "synthetic-key" not in response.text and "sk-ant-oat-test" not in response.text
    )


def test_connection(
    request: pytest.FixtureRequest,
    client: TestClient,
    completions: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orgs, _, _, users = request.getfixturevalue("tenant_data")
    auth = headers(users[0])
    url = f"/api/v1/orgs/{orgs[0].id}/model-setting"
    assert client.post(url + "/test", headers=auth).json() == {
        "ok": False,
        "message": "模型已关闭，请先启用模型",
    }
    assert (
        client.put(
            url,
            headers=auth,
            json=CLOUD_SETTING,
        ).status_code
        == 200
    )
    replies, _ = completions
    replies.append('{"ok":true}')
    assert client.post(url + "/test", headers=auth).json() == {
        "ok": True,
        "message": "模型连接成功",
    }
    for error in [
        ModelUnavailable("密钥不对或没有权限"),
        ModelUnavailable("地址或模型名不对"),
        ModelUnavailable("连不上模型服务"),
        ModelUnavailable("模型服务暂时不可用"),
        ModelRateLimited(None),
        ModelOutputInvalid(),
    ]:

        def fail(*_args: Any, _error: Exception = error, **_kwargs: Any) -> None:
            raise _error

        with monkeypatch.context() as patch:
            patch.setattr(route, "chat_json", fail)
            assert client.post(url + "/test", headers=auth).json() == {
                "ok": False,
                "message": str(error),
            }
    monkeypatch.setattr(settings, "SECRET_KEY", "changed-test-key")
    assert (
        client.post(url + "/test", headers=headers(users[0])).json()["message"]
        == "保存的密钥已失效，请重新填写"
    )


def test_json_and_parameters(
    completions: tuple, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture
) -> None:
    replies, calls = completions
    messages = [{"role": "user", "content": "synthetic-private-prompt"}]
    for model, provider, schema in [
        ("openai/gpt-4o-mini", "openai", True),
        ("anthropic/claude-sonnet-4-5", "anthropic", True),
        ("ollama_chat/llama3", "ollama_chat", False),
    ]:
        replies.extend(["bad", '```json\n{"ok":true}\n```'])
        result = chat_json(
            ModelConfig(model, api_key="synthetic-key"), messages, route.ModelTestOutput
        )
        assert (
            result.value.ok
            and result.provider == provider
            and result.model
            and result.attempts == 2
        )
        assert calls[-1]["temperature"] == 0 and calls[-1]["num_retries"] == 0
        assert calls[-1]["drop_params"] and calls[-1]["timeout"] == 120
        assert ("response_format" in calls[-1]) == schema
        assert calls[-1]["messages"][-2]["content"] == "bad"
    assert len(messages) == 1
    replies.extend(['{"ok":false}'] * 3)
    with pytest.raises(ModelOutputInvalid):
        chat_json(ModelConfig("openai/gpt-4o-mini"), messages, route.ModelTestOutput)
    with pytest.raises(ValueError):
        chat_json(
            ModelConfig("openai/gpt-4o-mini"), [], route.ModelTestOutput, attempts=0
        )
    captured = capsys.readouterr()
    for secret in ("synthetic-private-prompt", "synthetic-key"):
        assert secret not in caplog.text + captured.out + captured.err


@pytest.mark.parametrize(
    "name,reason,count",
    [
        ("AuthenticationError", "密钥不对或没有权限", 1),
        ("PermissionDeniedError", "密钥不对或没有权限", 1),
        ("NotFoundError", "地址或模型名不对", 1),
        ("BadRequestError", "地址或模型名不对", 1),
        ("APIConnectionError", "连不上模型服务", 3),
        ("Timeout", "模型服务暂时不可用", 3),
        ("InternalServerError", "模型服务暂时不可用", 3),
        ("ServiceUnavailableError", "模型服务暂时不可用", 3),
        ("BadGatewayError", "模型服务暂时不可用", 3),
        ("ContextWindowExceededError", "ContextWindowExceededError", 1),
    ],
)
def test_exceptions(name: str, reason: str, count: int, completions: tuple) -> None:
    replies, calls = completions
    error = getattr(_sdk(), name)(
        message=("model not found" if name == "BadRequestError" else "error")
        + " synthetic-key private-prompt",
        llm_provider="openai",
        model="gpt-4o-mini",
        **(
            {
                "response": httpx.Response(
                    403, request=httpx.Request("POST", "https://example.com")
                )
            }
            if name == "PermissionDeniedError"
            else {}
        ),
    )
    replies.extend([error] * 3)
    with pytest.raises(ModelUnavailable, match=reason) as raised:
        chat_json(ModelConfig("openai/gpt-4o-mini"), [], route.ModelTestOutput)
    assert "synthetic-key" not in str(raised.value) and len(calls) == count


def test_compatible_without_key(completions: tuple) -> None:
    replies, calls = completions
    replies.append('{"ok":true}')
    config = ModelConfig("openai/gpt-4o-mini", base_url="http://localhost:11434/v1")
    assert chat_json(config, [], route.ModelTestOutput).value.ok
    assert calls[-1]["api_key"] == "not-needed"
    sdk = _sdk()
    assert (
        sdk.llms.openai.openai.OpenAIChatCompletion()._get_openai_client(
            is_async=False, api_key=calls[-1]["api_key"], api_base=config.base_url
        )
        is not None
    )


def test_rate_limit(completions: tuple) -> None:
    replies, calls = completions
    error = _sdk().RateLimitError(
        "limited",
        "openai",
        "gpt-4o-mini",
        response=httpx.Response(
            429,
            headers={"Retry-After": "2"},
            request=httpx.Request("POST", "https://example.com"),
        ),
    )
    replies.extend([error, '{"ok":true}'])
    assert (
        chat_json(ModelConfig("openai/gpt-4o-mini"), [], route.ModelTestOutput).attempts
        == 2
    )
    replies.extend([error] * 3)
    with pytest.raises(ModelRateLimited) as raised:
        chat_json(ModelConfig("openai/gpt-4o-mini"), [], route.ModelTestOutput)
    assert raised.value.retry_at > datetime.now(UTC) and len(calls) == 5
    for value, valid in [
        (None, False),
        ("invalid", False),
        ("inf", False),
        (format_datetime(datetime.now(UTC) + timedelta(seconds=30)), True),
    ]:
        error.response.headers = httpx.Headers({"retry-after": value} if value else {})
        assert (_retry_at(error) is not None) == valid


def test_offline_subprocess() -> None:
    script = """
import socket, sys
connections = []
def blocked(*args, **kwargs):
    connections.append(True)
    raise RuntimeError("Network access forbidden")
socket.socket.connect = blocked
socket.socket.connect_ex = blocked
socket.create_connection = blocked
from app.domains.model.client import _sdk, chat_json, ModelConfig
from pydantic import BaseModel
assert "litellm" not in sys.modules
sdk = _sdk()
real = sdk.completion
sdk.completion = lambda **kw: real(**kw, mock_response='{"ok":true}')
class Output(BaseModel):
    ok: bool
for model in ["openai/gpt-4o-mini", "anthropic/claude-sonnet-4-5", "ollama_chat/llama3"]:
    assert chat_json(ModelConfig(model, api_key="synthetic-key"), [], Output).value.ok
assert not connections, connections
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_import_boundary() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--stdin-filename",
            "app/api/routes/forbidden.py",
            "-",
        ],
        input="import litellm\n",
        capture_output=True,
        text=True,
        check=False,
    )
    assert "TID251" in result.stdout and result.returncode == 1
