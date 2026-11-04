import json
import time
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import settings
from app.core.secrets import decrypt_secret, encrypt_secret
from app.domains.audit.models import AuditEvent
from app.domains.base import utc_now
from app.domains.model import chatgpt
from app.domains.model.client import ModelUnavailable
from app.domains.model.models import ModelSetting
from app.domains.model.service import model_config
from app.domains.org.models import OrgMembership, Role, RoleGrant
from tests.authz.test_tenant_access import headers, tenant_data  # noqa: F401
from tests.utils.pi import PiHarness


def record() -> dict[str, Any]:
    return {
        "access": "synthetic-access",
        "refresh": "synthetic-refresh",
        "expires": (time.time() + 7200) * 1000,
        "accountId": "synthetic-account",
    }


@pytest.fixture(autouse=True)
def auth_pi(pi_service: PiHarness) -> PiHarness:
    return pi_service


def callback(
    start: dict, code: str = "synthetic-code", state: str | None = None
) -> dict:
    state = state or parse_qs(urlsplit(start["verification_url"]).query)["state"][0]
    return {"input": f"http://localhost:1455/auth/callback?code={code}&state={state}"}


def test_login(
    request: pytest.FixtureRequest,
    client: TestClient,
    db: Session,
    caplog: pytest.LogCaptureFixture,
    auth_pi: PiHarness,
) -> None:
    orgs, _, _, users = request.getfixturevalue("tenant_data")
    url = f"/api/v1/orgs/{orgs[0].id}/model-setting"
    h = headers(users[0])
    assert client.get(url + "/chatgpt/login", headers=h).json()["status"] == "none"
    assert (
        client.put(
            url, headers=h, json={"enabled": True, "kind": "chatgpt_subscription"}
        ).status_code
        == 422
    )
    start = client.post(url + "/chatgpt/login", headers=h).json()
    assert (
        start["status"] == "pending"
        and start == client.post(url + "/chatgpt/login", headers=h).json()
    )
    setting = db.get(ModelSetting, orgs[0].id)
    assert "synthetic-state" not in setting.chatgpt_login_ciphertext
    result = client.post(url + "/chatgpt/callback", headers=h, json=callback(start))
    assert result.json()["status"] == "connected"
    assert (
        client.post(url + "/chatgpt/callback", headers=h, json=callback(start)).json()[
            "status"
        ]
        == "error"
    )
    saved = client.put(
        url, headers=h, json={"enabled": True, "kind": "chatgpt_subscription"}
    )
    assert (
        saved.json()["model"] == "chatgpt/gpt-5.4"
        and saved.json()["reasoning_effort"] == "high"
    )
    assert client.post(url + "/test", headers=h).json()["ok"]
    db.refresh(setting)
    assert (
        json.loads(decrypt_secret(setting.chatgpt_auth_ciphertext))["refresh"]
        == "synthetic-refresh"
    )
    for index, status in ((1, 403), (2, 403), (3, 404)):
        for method, path in (
            ("GET", "/chatgpt/login"),
            ("POST", "/chatgpt/login"),
            ("POST", "/chatgpt/callback"),
            ("DELETE", "/chatgpt/login"),
            ("DELETE", "/chatgpt"),
        ):
            assert (
                client.request(
                    method,
                    url + path,
                    headers=headers(users[index]),
                    json=callback(start) if method == "POST" else None,
                ).status_code
                == status
            )
    assert client.delete(url + "/chatgpt", headers=h).status_code == 200
    db.refresh(setting)
    assert (
        not setting.enabled
        and not setting.chatgpt_auth_ciphertext
        and not setting.chatgpt_account
    )
    events = db.exec(select(AuditEvent).where(AuditEvent.org_id == orgs[0].id)).all()
    assert any(e.action == "model_setting.chatgpt_connected" for e in events)
    for secret in (
        "synthetic-access",
        "synthetic-refresh",
        "synthetic-code",
        "synthetic-state",
    ):
        assert (
            secret
            not in result.text
            + saved.text
            + caplog.text
            + str([e.details for e in events])
            + auth_pi.logs.read_text()
        )


@pytest.mark.parametrize(
    "bad",
    [
        "code-only",
        "http://localhost:1455/auth/callback?code=x",
        "http://localhost:1455/auth/callback?code=x&state=wrong",
    ],
)
def test_invalid_state_consumes_login(
    bad: str, request: pytest.FixtureRequest, client: TestClient
) -> None:
    orgs, _, _, users = request.getfixturevalue("tenant_data")
    url, h = f"/api/v1/orgs/{orgs[0].id}/model-setting/chatgpt", headers(users[0])
    client.post(url + "/login", headers=h)
    assert (
        client.post(url + "/callback", headers=h, json={"input": bad}).json()["status"]
        == "error"
    )
    assert client.get(url + "/login", headers=h).json()["status"] == "none"
    assert client.post(url + "/login", headers=h).json()["status"] == "pending"
    assert client.delete(url + "/login", headers=h).json()["status"] == "none"


def test_login_binding_expiry_and_restart(
    request: pytest.FixtureRequest, client: TestClient, db: Session
) -> None:
    orgs, _, _, users = request.getfixturevalue("tenant_data")
    # Grant two existing tenant members admin access to exercise the binding beyond route auth.
    for org, user in ((orgs[0], users[1]), (orgs[1], users[3])):
        member = db.exec(
            select(OrgMembership).where(
                OrgMembership.org_id == org.id, OrgMembership.user_id == user.id
            )
        ).one()
        db.add(RoleGrant(org_id=org.id, membership_id=member.id, role=Role.ORG_ADMIN))
    db.commit()
    url = f"/api/v1/orgs/{orgs[0].id}/model-setting/chatgpt"
    other = f"/api/v1/orgs/{orgs[1].id}/model-setting/chatgpt"
    h = headers(users[0])
    start = client.post(url + "/login", headers=h).json()
    assert (
        "稍后再试"
        in client.post(other + "/login", headers=headers(users[3])).json()["message"]
    )
    assert (
        client.post(
            other + "/callback", headers=headers(users[3]), json=callback(start)
        ).json()["status"]
        == "error"
    )
    for path in ("/callback", "/login"):
        result = client.post(
            url + path, headers=headers(users[1]), json=callback(start)
        )
        assert result.json()["status"] == "error" and "verification_url" not in {
            k: v for k, v in result.json().items() if v
        }
    setting = db.get(ModelSetting, orgs[0].id)
    setting.chatgpt_login_expires_at = utc_now() - timedelta(seconds=1)
    db.commit()
    assert (
        client.post(url + "/callback", headers=h, json=callback(start)).json()["status"]
        == "expired"
    )
    start = client.post(url + "/login", headers=h).json()
    db.refresh(setting)
    login = json.loads(decrypt_secret(setting.chatgpt_login_ciphertext))
    chatgpt.auth_request("DELETE", "/" + login["login_id"])
    assert (
        "重新开始"
        in client.post(url + "/callback", headers=h, json=callback(start)).json()[
            "message"
        ]
    )
    start = client.post(url + "/login", headers=h).json()
    assert (
        client.post(
            url + "/callback", headers=h, json=callback(start, code="failure")
        ).json()["status"]
        == "error"
    )
    assert client.post(url + "/login", headers=h).json()["status"] == "pending"
    client.delete(url, headers=h)


@pytest.fixture
def connected(request: pytest.FixtureRequest, db: Session) -> ModelSetting:
    orgs, _, _, users = request.getfixturevalue("tenant_data")
    setting = ModelSetting(
        org_id=orgs[0].id,
        updated_by_user_id=users[0].id,
        enabled=True,
        kind="chatgpt_subscription",
        model="chatgpt/gpt-5.4",
        reasoning_effort="high",
        chatgpt_auth_ciphertext=encrypt_secret(json.dumps(record())),
        chatgpt_account={"needs_relogin": False},
    )
    db.add(setting)
    db.commit()
    return setting


@pytest.mark.parametrize("invalid", ["expired", "old", "key"])
def test_reconnect(
    invalid: str,
    connected: ModelSetting,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    request: pytest.FixtureRequest,
) -> None:
    _, _, _, users = request.getfixturevalue("tenant_data")
    auth = headers(users[0])
    if invalid == "key":
        monkeypatch.setattr(settings, "SECRET_KEY", "changed")
        auth = headers(users[0])
    else:
        connected.chatgpt_auth_ciphertext = encrypt_secret(
            json.dumps(
                {"access_token": "old"}
                if invalid == "old"
                else {**record(), "expires": time.time() * 1000 + 1000}
            )
        )
        db.commit()
    with pytest.raises(ModelUnavailable, match="重新连接"):
        model_config(connected)
    result = client.get(
        f"/api/v1/orgs/{connected.org_id}/model-setting/chatgpt/login", headers=auth
    )
    assert result.status_code == 200 and result.json()["account"]["needs_relogin"]
    db.refresh(connected)
    assert connected.chatgpt_account["needs_relogin"]


def test_unauthorized_and_limits(
    connected: ModelSetting,
    request: pytest.FixtureRequest,
    client: TestClient,
    auth_pi: PiHarness,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _, _, _, users = request.getfixturevalue("tenant_data")
    url, h = f"/api/v1/orgs/{connected.org_id}/model-setting/test", headers(users[0])
    auth_pi.replies.append({"status": 429, "retry_after": 120})
    assert "用量到上限" in client.post(url, headers=h).json()["message"]
    auth_pi.replies.append({"status": 401})
    assert "重新连接" in client.post(url, headers=h).json()["message"]
    assert "sensitive upstream" not in caplog.text + auth_pi.logs.read_text()


def test_old_failure_cannot_invalidate_new_login(
    connected: ModelSetting, db: Session
) -> None:
    connected.chatgpt_auth_ciphertext = encrypt_secret(
        json.dumps({**record(), "access": "new-synthetic-access"})
    )
    db.commit()
    chatgpt.invalidate(connected.org_id, "synthetic-access")
    db.refresh(connected)
    assert not connected.chatgpt_account["needs_relogin"]
