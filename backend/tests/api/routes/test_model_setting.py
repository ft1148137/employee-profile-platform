import subprocess
import sys
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.api.routes import model_setting as route
from app.core.config import settings
from app.core.secrets import SecretUnavailable, decrypt_secret, encrypt_secret
from app.domains.audit.models import AuditEvent
from app.domains.model.client import (
    ModelOutputInvalid,
    ModelRateLimited,
    ModelServiceError,
    ModelUnavailable,
)
from app.domains.model.models import ModelSetting
from tests.authz.test_tenant_access import headers, tenant_data  # noqa: F401
from tests.utils.pi import PiHarness

CLOUD_SETTING = {
    "enabled": True,
    "kind": "cloud_api",
    "provider": "openai",
    "model": "gpt-4o-mini",
    "api_key": "synthetic-key",
}


@pytest.fixture(autouse=True)
def setting_pi(pi_service: PiHarness) -> PiHarness:
    return pi_service


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
        "provider": None,
        "model": None,
        "base_url": None,
        "has_api_key": False,
        "reasoning_effort": None,
        "chatgpt": {
            "account_id": None,
            "connected": False,
            "email": None,
            "plan_type": None,
            "needs_relogin": False,
        },
        "paused_until": None,
        "pause_reason": None,
        "updated_at": None,
    }
    body = {
        **CLOUD_SETTING,
        "kind": "openai_compatible",
        "provider": "openai-compatible",
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
        "kind": "openai_compatible",
        "provider": "openai-compatible",
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
    for update in (
        {"provider": "unknown"},
        {"kind": "anthropic", "api_key": "synthetic-key"},
    ):
        assert client.put(url, headers=auth, json={**body, **update}).status_code == 422
    assert (
        client.put(
            url,
            headers=auth,
            json={**body, "kind": "local", "provider": "ollama", "api_key": ""},
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
        {"kind": "cloud_api", "provider": "anthropic", "base_url": None},
        {"kind": "local", "provider": "ollama"},
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
    body = {
        **CLOUD_SETTING,
        "kind": "openai_compatible",
        "provider": "openai-compatible",
        "enabled": enabled,
        "base_url": "https://example.com/v1",
    }
    assert client.put(url, headers=auth, json=body).status_code == 200
    stored = db.get(ModelSetting, orgs[0].id)
    assert stored
    before = stored.model_dump()
    audit_query = select(AuditEvent).where(AuditEvent.org_id == orgs[0].id)
    audit_ids = [event.id for event in db.exec(audit_query).all()]
    body.pop("api_key")
    response = client.put(url, headers=auth, json={**body, **update, **key_input})
    assert response.status_code == 422
    if (
        not enabled
        or update.get("base_url", "unchanged") is not None
        or "provider" in update
    ):
        assert "更换了模型类型、提供方或服务地址，请重新填写密钥" in response.text
    db.refresh(stored)
    assert stored.model_dump() == before
    assert [event.id for event in db.exec(audit_query).all()] == audit_ids
    assert "synthetic-key" not in response.text + caplog.text


@pytest.mark.parametrize(
    "update,expected_key",
    [
        ({"model": "another"}, "synthetic-key"),
        ({"base_url": "https://example.com/v1/"}, "synthetic-key"),
        ({"base_url": "https://example.com/v1///"}, "synthetic-key"),
        (
            {
                "base_url": "https://other.example.com/v1",
                "api_key": "synthetic-new-key",
            },
            "synthetic-new-key",
        ),
        (
            {
                "kind": "local",
                "provider": "ollama",
                "model": "llama3",
                "base_url": "http://localhost:11434",
                "api_key": "",
            },
            None,
        ),
    ],
)
def test_saved_key_destination_updates(
    update: dict[str, Any],
    expected_key: str | None,
    request: pytest.FixtureRequest,
    client: TestClient,
    db: Session,
) -> None:
    orgs, _, _, users = request.getfixturevalue("tenant_data")
    url = f"/api/v1/orgs/{orgs[0].id}/model-setting"
    auth = headers(users[0])
    body = {
        **CLOUD_SETTING,
        "kind": "openai_compatible",
        "provider": "openai-compatible",
        "base_url": "https://example.com/v1",
    }
    assert client.put(url, headers=auth, json=body).status_code == 200
    stored = db.get(ModelSetting, orgs[0].id)
    old_token = stored.api_key_ciphertext
    body.pop("api_key")
    response = client.put(url, headers=auth, json={**body, **update})
    assert response.status_code == 200
    db.refresh(stored)
    assert stored.base_url == update.get("base_url", body["base_url"])
    assert response.json()["has_api_key"] == bool(expected_key)
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
        {"provider": None},
        {"provider": "unknown"},
        {"model": ""},
        {"model": "x" * 201},
        {"base_url": "x" * 501},
        {"base_url": "ftp://example.com"},
        {"base_url": "https://user:pass@example.com"},
        {"kind": "local", "base_url": None},
        {"kind": "openai_compatible", "base_url": None},
        {"kind": "cloud_api", "base_url": "https://example.com/v1"},
        {"kind": "chatgpt_subscription"},
        {"kind": "chatgpt_subscription", "provider": "anthropic"},
        {"model": "chatgpt/gpt-5"},
        {"model": "github_copilot/gpt-5"},
        {"provider": "anthropic", "model": "claude", "api_key": "sk-ant-oat-test"},
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
    setting_pi: PiHarness,
    db: Session,
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
    setting = db.get(ModelSetting, orgs[0].id)
    setting.kind, setting.provider, setting.base_url = (
        "openai_compatible",
        "openai-compatible",
        setting_pi.config.baseUrl,
    )
    db.add(setting)
    db.commit()
    assert client.post(url + "/test", headers=auth).json() == {
        "ok": True,
        "message": "模型连接成功",
    }
    for error in [
        ModelServiceError("model_not_found"),
        ModelServiceError("upstream_rejected"),
        ModelServiceError("upstream_unavailable"),
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
            patch.setattr(route, "run_turn", fail)
            assert client.post(url + "/test", headers=auth).json() == {
                "ok": False,
                "message": str(error),
            }
    monkeypatch.setattr(settings, "SECRET_KEY", "changed-test-key")
    assert (
        client.post(url + "/test", headers=headers(users[0])).json()["message"]
        == "保存的密钥已失效，请重新填写"
    )


def test_import_boundary() -> None:
    for sdk in ("openai", "anthropic"):
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
            input=f"import {sdk}\n",
            capture_output=True,
            text=True,
            check=False,
        )
        assert "TID251" in result.stdout and result.returncode == 1


@pytest.mark.parametrize("initial_url,new_url", [(None, ""), ("", None)])
def test_cloud_empty_destination_preserves_key(
    initial_url: str | None,
    new_url: str | None,
    request: pytest.FixtureRequest,
    client: TestClient,
    db: Session,
) -> None:
    orgs, _, _, users = request.getfixturevalue("tenant_data")
    url, auth = f"/api/v1/orgs/{orgs[0].id}/model-setting", headers(users[0])
    assert (
        client.put(url, headers=auth, json={**CLOUD_SETTING, "api_key": ""}).status_code
        == 422
    )
    assert (
        client.put(
            url, headers=auth, json={**CLOUD_SETTING, "base_url": initial_url}
        ).status_code
        == 200
    )
    token = db.get(ModelSetting, orgs[0].id).api_key_ciphertext
    body = {k: v for k, v in CLOUD_SETTING.items() if k != "api_key"}
    result = client.put(url, headers=auth, json={**body, "base_url": new_url})
    assert result.status_code == 200
    db.expire_all()
    assert db.get(ModelSetting, orgs[0].id).api_key_ciphertext == token
    assert result.json()["has_api_key"]


def test_keyless_local_destination_change(
    request: pytest.FixtureRequest, client: TestClient
) -> None:
    orgs, _, _, users = request.getfixturevalue("tenant_data")
    url, auth = f"/api/v1/orgs/{orgs[0].id}/model-setting", headers(users[0])
    body = {
        "enabled": True,
        "kind": "local",
        "provider": "ollama",
        "model": "synthetic",
        "base_url": "http://localhost:11434",
    }
    assert client.put(url, headers=auth, json=body).status_code == 200
    result = client.put(
        url, headers=auth, json={**body, "base_url": "http://localhost:11435"}
    )
    assert result.status_code == 200 and not result.json()["has_api_key"]


def test_unsupported_reasoning_save(
    request: pytest.FixtureRequest, client: TestClient
) -> None:
    orgs, _, _, users = request.getfixturevalue("tenant_data")
    url, auth = f"/api/v1/orgs/{orgs[0].id}/model-setting", headers(users[0])
    result = client.put(
        url, headers=auth, json={**CLOUD_SETTING, "reasoning_effort": "high"}
    )
    assert result.status_code == 422 and "推理强度 high 不适用于该模型" in result.text


@pytest.mark.parametrize(
    "status,reason",
    [
        (401, "密钥不对或没有权限"),
        (403, "模型服务拒绝了请求，请检查模型设置"),
        (404, "这个模型不可用，请换一个模型"),
        (400, "模型服务拒绝了请求，请检查模型设置"),
        (500, "模型服务暂时不可用，请稍后重试"),
    ],
)
def test_exceptions(
    status: int, reason: str, setting_pi: PiHarness, caplog: pytest.LogCaptureFixture
) -> None:
    from app.domains.model.pi_client import run_turn

    setting_pi.replies.append({"status": status})
    with pytest.raises(ModelUnavailable, match=reason) as error:
        run_turn(
            setting_pi.config,
            system_prompt="private-prompt",
            input="private-prompt",
            tools=[],
            on_tool_call=lambda *_: None,
        )
    assert (
        "sensitive" not in str(error.value) + caplog.text + setting_pi.logs.read_text()
    )
    assert len(setting_pi.requests) == 1


def test_compatible_without_key(setting_pi: PiHarness) -> None:
    from pydantic import SecretStr

    from app.domains.model.pi_client import run_turn

    setting_pi.config.api_key = SecretStr("")
    assert (
        run_turn(
            setting_pi.config,
            system_prompt="",
            input="",
            tools=[],
            on_tool_call=lambda *_: None,
        ).stop_reason
        == "stop"
    )


def test_offline_subprocess() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import socket; socket.socket.connect = lambda *a: (_ for _ in ()).throw(AssertionError('network')); import app.domains.model.service",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
