import inspect
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.api.routes.model_setting import ModelTestOutput
from app.core.config import Settings, settings
from app.core.secrets import decrypt_secret, encrypt_secret
from app.domains.audit.models import AuditEvent
from app.domains.model import chatgpt
from app.domains.model.client import (
    ModelRateLimited,
    ModelUnavailable,
    _sdk,
    chat_json,
    rate_limit,
)
from app.domains.model.models import ModelSetting
from app.domains.model.service import model_config
from tests.authz.test_tenant_access import headers, tenant_data  # noqa: F401

pytestmark = pytest.mark.xdist_group("litellm")


def token(**claims: Any) -> str:
    return jwt.encode(claims, "synthetic-key", algorithm="HS256")


def record() -> dict[str, Any]:
    return {
        "access_token": token(exp=int(time.time()) + 7200),
        "refresh_token": "synthetic-refresh",
        "id_token": token(
            email="synthetic@example.com",
            **{
                "https://api.openai.com/auth": {
                    "chatgpt_plan_type": "plus",
                    "chatgpt_account_id": "synthetic-account",
                }
            },
        ),
    }


def test_internal_contract() -> None:
    auth, constants = chatgpt.sdk_auth()
    assert (
        __import__(
            type(auth).__module__, fromlist=["OPENAI_AUTH_CLAIM_KEY"]
        ).OPENAI_AUTH_CLAIM_KEY
        == "https://api.openai.com/auth"
    )
    for method, args in {
        "_request_device_code": [],
        "_poll_for_authorization_code": ["device_code"],
        "_exchange_code_for_tokens": ["code_data"],
        "_build_auth_record": ["tokens"],
        "_decode_jwt_claims": ["token"],
        "_get_expires_at": ["token"],
    }.items():
        assert list(inspect.signature(getattr(auth, method)).parameters) == args
    for name in (
        "CHATGPT_DEVICE_VERIFY_URL",
        "CHATGPT_DEVICE_TOKEN_URL",
        "CHATGPT_CLIENT_ID",
    ):
        assert getattr(constants, name)
    assert auth.auth_file == str(chatgpt.TOKEN_FILE)
    assert chatgpt.TOKEN_DIR.stat().st_mode & 0o777 == 0o700


def test_login(
    request: pytest.FixtureRequest,
    client: TestClient,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    orgs, _, _, users = request.getfixturevalue("tenant_data")
    url = f"/api/v1/orgs/{orgs[0].id}/model-setting"
    auth, _ = chatgpt.sdk_auth()
    code = {
        "device_auth_id": "synthetic-device",
        "user_code": "ABCD-EFGH",
        "interval": "5",
    }
    monkeypatch.setattr(type(auth), "_request_device_code", lambda _: dict(code))
    monkeypatch.setattr(
        type(auth), "_exchange_code_for_tokens", lambda _self, _data: record()
    )
    statuses: list[int] = [403, 404, 500, 200, 200]
    calls: list[httpx.Request] = []

    def transport(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        status = statuses.pop(0)
        return httpx.Response(
            status,
            json={
                "authorization_code": "code",
                "code_verifier": "verifier",
                "code_challenge": "challenge",
            }
            if len(statuses) == 0
            else {},
        )

    real_client = httpx.Client
    monkeypatch.setattr(
        chatgpt.httpx,
        "Client",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(transport), **kwargs
        ),
    )
    h = headers(users[0])
    assert client.get(url + "/chatgpt/login", headers=h).json()["status"] == "none"
    assert (
        client.put(
            url, headers=h, json={"enabled": True, "kind": "chatgpt_subscription"}
        ).status_code
        == 422
    )
    start = client.post(url + "/chatgpt/login", headers=h).json()
    assert start["user_code"] == code["user_code"] and start["verification_url"]
    assert client.post(url + "/chatgpt/login", headers=h).json() == start
    setting = db.get(ModelSetting, orgs[0].id)
    assert setting and "synthetic-device" not in setting.chatgpt_login_ciphertext
    for expected in ("pending", "pending", "error", "error", "connected"):
        setting.chatgpt_login_polled_at = datetime.now(UTC) - timedelta(seconds=6)
        db.commit()
        result = client.get(url + "/chatgpt/login", headers=h)
        assert result.json()["status"] == expected
        count = len(calls)
        if expected != "connected":
            assert (
                client.get(url + "/chatgpt/login", headers=h).json()["status"]
                == "pending"
            )
            assert len(calls) == count
        db.refresh(setting)
    assert (
        setting.chatgpt_account["plan_type"] == "plus"
        and setting.chatgpt_account["email"] == "synthetic@example.com"
    )
    assert (
        decrypt_secret(setting.chatgpt_auth_ciphertext)
        and "synthetic-refresh" not in setting.chatgpt_auth_ciphertext
    )
    saved = client.put(
        url, headers=h, json={"enabled": True, "kind": "chatgpt_subscription"}
    )
    assert (
        saved.json()["model"] == "chatgpt/gpt-6.1-sol"
        and saved.json()["reasoning_effort"] == "high"
    )
    assert client.get(url + "/chatgpt/login", headers=h).json()["status"] == "connected"
    for index, status in ((1, 403), (2, 403), (3, 404)):
        for method, path in (
            ("GET", "/chatgpt/login"),
            ("POST", "/chatgpt/login"),
            ("DELETE", "/chatgpt"),
        ):
            assert (
                client.request(
                    method, url + path, headers=headers(users[index])
                ).status_code
                == status
            )
    client.post(url + "/chatgpt/login", headers=h)
    db.refresh(setting)
    setting.chatgpt_login_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    assert client.get(url + "/chatgpt/login", headers=h).json()["status"] == "expired"
    assert client.delete(url + "/chatgpt", headers=h).status_code == 200
    db.refresh(setting)
    assert (
        not setting.enabled
        and not setting.chatgpt_auth_ciphertext
        and not setting.chatgpt_account
    )
    client.post(url + "/chatgpt/login", headers=h)
    db.refresh(setting)
    setting.chatgpt_login_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    assert client.get(url + "/chatgpt/login", headers=h).json()["status"] == "expired"
    events = db.exec(select(AuditEvent).where(AuditEvent.org_id == orgs[0].id)).all()
    assert any(
        e.action == "model_setting.chatgpt_connected"
        and e.details == {"plan_type": "plus"}
        for e in events
    )
    for secret in ("synthetic-refresh", "synthetic-device", "ABCD-EFGH"):
        assert secret not in result.text + saved.text + caplog.text + str(
            [e.details for e in events]
        )


@pytest.fixture
def connected(request: pytest.FixtureRequest, db: Session) -> ModelSetting:
    orgs, _, _, users = request.getfixturevalue("tenant_data")
    setting = ModelSetting(
        org_id=orgs[0].id,
        updated_by_user_id=users[0].id,
        enabled=True,
        kind="chatgpt_subscription",
        model="chatgpt/gpt-6.1-sol",
        reasoning_effort="high",
        chatgpt_auth_ciphertext=encrypt_secret(json.dumps(record())),
        chatgpt_account={"needs_relogin": False},
    )
    db.add(setting)
    db.commit()
    return setting


def test_calls(
    connected: ModelSetting,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sdk = _sdk()
    real = sdk.completion
    seen: list[str] = []

    def complete(**kw: Any) -> Any:
        assert "response_format" not in kw and kw["reasoning_effort"] == "high"
        assert chatgpt.TOKEN_FILE.stat().st_mode & 0o777 == 0o600
        data = json.loads(chatgpt.TOKEN_FILE.read_text())
        seen.append(data["refresh_token"])
        data["refresh_token"] += "-refreshed"
        chatgpt.TOKEN_FILE.write_text(json.dumps(data))
        time.sleep(0.03)
        return real(**kw, mock_response='{"ok":true}')

    monkeypatch.setattr(sdk, "completion", complete)
    config = model_config(connected)
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(chat_json, config, [], ModelTestOutput) for _ in range(2)]
        assert all(job.result(timeout=15).value.ok for job in jobs)
    db.refresh(connected)
    assert seen == ["synthetic-refresh", "synthetic-refresh-refreshed"]
    assert json.loads(decrypt_secret(connected.chatgpt_auth_ciphertext))[
        "refresh_token"
    ].endswith("-refreshed-refreshed")
    assert not chatgpt.TOKEN_FILE.exists()
    auth, constants = chatgpt.sdk_auth()

    def denied(_self: Any, _refresh: str) -> None:
        raise constants.RefreshAccessTokenError(
            message="synthetic-refresh", status_code=401
        )

    monkeypatch.setattr(type(auth), "_refresh_tokens", denied)
    monkeypatch.setattr(sdk, "completion", lambda **_kw: auth.get_access_token())
    connected.chatgpt_auth_ciphertext = encrypt_secret(
        json.dumps({**record(), "access_token": token(exp=1)})
    )
    db.commit()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(chat_json, config, [], ModelTestOutput)
        with pytest.raises(ModelUnavailable, match="重新连接"):
            future.result(timeout=5)
    db.refresh(connected)
    assert (
        connected.chatgpt_account["needs_relogin"] and not chatgpt.TOKEN_FILE.exists()
    )
    connected.chatgpt_account = {"needs_relogin": False}
    db.commit()
    for code, expected in ((401, ModelUnavailable), (429, ModelRateLimited)):

        def fail(_code: int = code, **_kw: Any) -> None:
            error_type = sdk.AuthenticationError if _code == 401 else sdk.RateLimitError
            raise error_type(
                "synthetic-refresh",
                "chatgpt",
                "gpt-6.1-sol",
                response=httpx.Response(
                    _code, request=httpx.Request("POST", "https://example.com")
                ),
            )

        monkeypatch.setattr(sdk, "completion", fail)
        with pytest.raises(expected):
            chat_json(config, [], ModelTestOutput, attempts=1)
        assert not chatgpt.TOKEN_FILE.exists()
        db.refresh(connected)
        connected.chatgpt_account = {"needs_relogin": False}
        db.commit()
    captured = capsys.readouterr()
    assert "synthetic-refresh" not in captured.out + captured.err + caplog.text
    assert "Enter code" not in captured.out


@pytest.mark.parametrize(
    "body,headers,seconds,reason",
    [
        (
            {"limit_window_minutes": 300, "resets_in_seconds": 100},
            {"Retry-After": "20"},
            20,
            "5 小时",
        ),
        ({"limit_window_minutes": 10080, "resets_in_seconds": 40}, {}, 40, "本周"),
        ({"resets_at": 2000000000}, {}, None, "请求过于频繁"),
        ({}, {}, 1800, "预计恢复时间未知"),
        ({"resets_in_seconds": "invalid"}, {}, 1800, "预计恢复时间未知"),
    ],
)
def test_limits(body: dict, headers: dict, seconds: int | None, reason: str) -> None:
    error = _sdk().RateLimitError(
        "private",
        "chatgpt",
        "gpt-6.1-sol",
        response=httpx.Response(
            429,
            json={"error": body},
            headers=headers,
            request=httpx.Request("POST", "https://example.com"),
        ),
    )
    now = datetime.now(UTC)
    retry, message = rate_limit(error)
    assert reason in message
    assert (
        abs((retry - now).total_seconds() - seconds) < 2
        if seconds
        else retry.timestamp() == 2000000000
    )


def test_local_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, connected: ModelSetting
) -> None:
    path = tmp_path / "codex.json"
    path.write_text(json.dumps({"tokens": record()}))
    original = path.read_bytes()
    monkeypatch.setattr(settings, "CHATGPT_CODEX_AUTH_FILE", path)

    def call() -> bool:
        assert "refresh_token" not in json.loads(chatgpt.TOKEN_FILE.read_text())
        return True

    assert chatgpt.completion(connected.org_id, call)
    assert path.read_bytes() == original and not chatgpt.TOKEN_FILE.exists()
    path.write_text(
        json.dumps(
            {"tokens": {**record(), "access_token": token(exp=time.time() + 100)}}
        )
    )
    with pytest.raises(ModelUnavailable, match="即将过期"):
        chatgpt.completion(connected.org_id, call)
    path.write_text(json.dumps({"tokens": {**record(), "access_token": "unreadable"}}))
    with pytest.raises(ModelUnavailable, match="即将过期"):
        chatgpt.completion(connected.org_id, call)
    path.write_text("invalid")
    with pytest.raises(ModelUnavailable, match="无法读取"):
        chatgpt.local_auth()
    with pytest.raises(ValueError, match="只允许在开发模式"):
        Settings(
            FASTAPI_ENV=None,
            CHATGPT_CODEX_AUTH_FILE=path,
            SECRET_KEY="synthetic-secure",
            DATABASE_URL="postgresql://postgres:synthetic@localhost/app",
            FIRST_SUPERUSER_PASSWORD="synthetic-password",
        )


@pytest.mark.chatgpt_live
@pytest.mark.skipif(
    not settings.CHATGPT_CODEX_AUTH_FILE
    or not settings.CHATGPT_CODEX_AUTH_FILE.exists(),
    reason="Local read-only Codex mount required",
)
def test_live(connected: ModelSetting, monkeypatch: pytest.MonkeyPatch) -> None:
    send = httpx.Client.send
    sent = []

    def checked_send(
        self: httpx.Client, request: httpx.Request, **kwargs: Any
    ) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/responses"):
            assert "refresh_token" not in json.loads(chatgpt.TOKEN_FILE.read_text())
            assert json.loads(request.content)["reasoning"]["effort"] == "high"
            sent.append(True)
        return send(self, request, **kwargs)

    monkeypatch.setattr(httpx.Client, "send", checked_send)
    assert chat_json(
        model_config(connected),
        [
            {
                "role": "user",
                "content": 'Synthetic connectivity test. Return {"ok":true}.',
            }
        ],
        ModelTestOutput,
        attempts=1,
    ).value.ok
    assert sent
