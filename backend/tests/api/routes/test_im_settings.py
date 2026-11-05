import json
import logging

import httpx
import pytest
import sentry_sdk
from fastapi.testclient import TestClient
from sqlalchemy import Engine, inspect, text
from sqlmodel import Session, select

from app.core.secrets import decrypt_secret
from app.domains.audit.models import AuditEvent
from app.domains.im.models import ImSetting
from tests.utils.migrations import migrate

CREDS = {
    "feishu": {"app_id": "synthetic-app", "app_secret": "synthetic-app-secret"},
    "wecom": {
        "bot_id": "synthetic-bot",
        "secret": "synthetic-wecom-secret",
        "corp_id": "synthetic-corp",
        "contact_secret": "synthetic-contact-secret",
    },
    "telegram": {"bot_token": "12345:synthetic-token"},
}


def test_settings(
    demo: tuple, client: TestClient, db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    base, people, auth = demo
    url = base + "/im-settings"
    caplog.set_level(logging.DEBUG)
    defaults = client.get(url, headers=auth["admin"]).json()
    assert len(defaults) == 3 and all(not row["enabled"] for row in defaults)
    for platform, credentials in CREDS.items():
        body = {
            "enabled": True,
            "credentials": credentials,
            "data_transfer_acknowledged": True,
        }
        response = client.put(f"{url}/{platform}", headers=auth["admin"], json=body)
        assert response.status_code == 200, response.text
        assert response.json()["has_credentials"] and response.json()["updated_at"]
        row = db.get(ImSetting, (people["E02"].org_id, platform))
        assert row and row.credentials
        assert json.loads(decrypt_secret(row.credentials)) == credentials
        assert row.email_auto_bind == (platform != "telegram")
        old = row.credentials
        assert (
            client.put(
                f"{url}/{platform}",
                headers=auth["admin"],
                json={
                    "enabled": False,
                    "email_auto_bind": False,
                    "credentials": dict.fromkeys(credentials, ""),
                },
            ).status_code
            == 200
        )
        db.refresh(row)
        assert not row.enabled and not row.email_auto_bind and row.credentials == old
        assert (
            client.put(
                f"{url}/{platform}", headers=auth["admin"], json={"enabled": True}
            ).status_code
            == 422
        )
        assert (
            client.put(
                f"{url}/{platform}",
                headers=auth["admin"],
                json={"enabled": True, "data_transfer_acknowledged": True},
            ).status_code
            == 200
        )
        audits = db.exec(
            select(AuditEvent).where(
                AuditEvent.org_id == row.org_id,
                AuditEvent.action == "im_settings.updated",
            )
        ).all()
        assert any(set(credentials) <= set(a.details["fields"]) for a in audits)
        outputs = (
            response.text
            + client.get(url, headers=auth["admin"]).text
            + repr(audits)
            + caplog.text
            + row.credentials
        )
        assert all(secret not in outputs for secret in credentials.values())
    response = client.put(
        url + "/feishu",
        headers=auth["admin"],
        json={"credentials": {"app_secret": "updated-secret"}},
    )
    assert response.status_code == 200
    row = db.get(ImSetting, (people["E02"].org_id, "feishu"))
    db.refresh(row)
    assert json.loads(decrypt_secret(row.credentials)) == {
        **CREDS["feishu"],
        "app_secret": "updated-secret",
    }


@pytest.mark.parametrize("platform", ["telegram", "wecom"])
def test_permissions(shared_demo: tuple, client: TestClient, platform: str) -> None:
    base, _, auth = shared_demo
    for role, status in [("manager", 403), ("employee", 403), ("foreign", 404)]:
        for method, suffix, body in [
            ("GET", "", None),
            ("PUT", f"/{platform}", {"credentials": CREDS[platform]}),
            ("POST", f"/{platform}/test", None),
        ]:
            assert (
                client.request(
                    method,
                    base + "/im-settings" + suffix,
                    headers=auth[role],
                    json=body,
                ).status_code
                == status
            )


def test_validation(demo: tuple, client: TestClient) -> None:
    base, _, auth = demo
    for body in [
        {"enabled": True, "data_transfer_acknowledged": True},
        {"credentials": {"app_secret": "private-value"}},
        {"credentials": {"bot_token": ["private-value"]}},
        {"credentials": {"bot_token": "private-value"}},
        {"credentials": {"bot_token": "123:x/getMe?leak"}},
        {"credentials": {"bot_token": "x" * 4097}},
    ]:
        response = client.put(
            base + "/im-settings/telegram", headers=auth["admin"], json=body
        )
        assert response.status_code == 422
        assert "private-value" not in response.text


def test_connection(
    demo: tuple,
    client: TestClient,
    db: Session,
    telegram_server: dict,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    base, people, auth = demo
    url = base + "/im-settings/telegram"
    caplog.set_level(logging.DEBUG)
    assert (
        client.post(url + "/test", headers=auth["admin"]).json()["message"]
        == "请先保存应用凭据"
    )
    assert (
        client.put(
            url, headers=auth["admin"], json={"credentials": CREDS["telegram"]}
        ).status_code
        == 200
    )
    with sentry_sdk.init(dsn="", default_integrations=True):

        def unexpected_release() -> None:
            pytest.fail("Telegram requests must not auto-detect a Sentry release")

        monkeypatch.setattr("sentry_sdk.client.get_default_release", unexpected_release)
        with sentry_sdk.isolation_scope() as scope:
            assert client.post(url + "/test", headers=auth["admin"]).json() == {
                "ok": True,
                "message": "连接成功",
            }
            assert "synthetic-token" not in json.dumps(scope._breadcrumbs, default=str)
    assert telegram_server["paths"] == ["/bot12345:synthetic-token/getMe"]
    for status, body, message in [
        (401, b"private-upstream-body", "应用凭据无效或没有权限"),
        (500, b"private-upstream-body", "平台服务暂时不可用"),
        (302, b"private-upstream-body", "平台服务暂时不可用"),
        (200, b"private-upstream-body", "平台返回无效响应"),
        (
            200,
            b'{"ok": false, "description": "private-upstream-body"}',
            "平台返回无效响应",
        ),
    ]:
        telegram_server.update(status=status, body=body)
        response = client.post(url + "/test", headers=auth["admin"])
        assert response.json() == {"ok": False, "message": message}
        assert "private-upstream-body" not in response.text + caplog.text
    for error, message in [
        (httpx.TimeoutException, "连接超时，请检查网络"),
        (httpx.ConnectError, "无法连接平台，请检查网络"),
    ]:

        async def fail(
            *_args: object, _error: type[Exception] = error, **_kwargs: object
        ) -> None:
            raise _error("private-upstream-body")

        with monkeypatch.context() as patch:
            patch.setattr(httpx.AsyncClient, "request", fail)
            assert (
                client.post(url + "/test", headers=auth["admin"]).json()["message"]
                == message
            )
    row = db.get(ImSetting, (people["E02"].org_id, "telegram"))
    row.credentials = "invalid-ciphertext"
    db.add(row)
    db.commit()
    assert (
        client.post(url + "/test", headers=auth["admin"]).json()["message"]
        == "保存的密钥已失效，请重新填写"
    )
    assert (
        "synthetic-token" not in caplog.text
        and "private-upstream-body" not in caplog.text
    )
    for platform in ("feishu", "wecom"):
        assert not client.post(
            base + f"/im-settings/{platform}/test", headers=auth["admin"]
        ).json()["ok"]


def test_migration(migration_engine: Engine) -> None:
    migrate(migration_engine, "downgrade", "a98e001bc001")
    with migration_engine.connect() as connection:
        assert "im_setting" not in inspect(connection).get_table_names()
    migrate(migration_engine, "upgrade", "head")
    with migration_engine.connect() as connection:
        assert inspect(connection).get_pk_constraint("im_setting")[
            "constrained_columns"
        ] == ["org_id", "platform"]
        assert (
            connection.execute(
                text("SELECT count(*) FROM alembic_version")
            ).scalar_one()
            == 1
        )
        assert any(
            fk["constrained_columns"] == ["org_id", "updated_by_user_id"]
            for fk in inspect(connection).get_foreign_keys("im_setting")
        )


@pytest.mark.parametrize("platform", ["feishu", "wecom", "telegram"])
def test_omitted_auto_bind_preserves_setting(
    demo: tuple, client: TestClient, platform: str
) -> None:
    base, _, auth = demo
    url = f"{base}/im-settings/{platform}"
    assert (
        client.put(url, headers=auth["admin"], json={"email_auto_bind": False}).json()[
            "email_auto_bind"
        ]
        is False
    )
    response = client.put(url, headers=auth["admin"], json={"enabled": False})
    assert response.status_code == 200
    assert response.json()["email_auto_bind"] is False
    explicit = client.put(url, headers=auth["admin"], json={"email_auto_bind": True})
    assert explicit.status_code == 200
    assert explicit.json()["email_auto_bind"] == (platform != "telegram")
