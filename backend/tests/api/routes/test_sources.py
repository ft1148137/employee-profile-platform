import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.connectors.jira.client import (
    NETWORK_MESSAGE,
    RetryableSourceError,
    jira_client_for,
)
from app.connectors.jira.text import adf_to_text, wiki_to_text
from app.core.config import settings
from app.core.secrets import decrypt_secret, encrypt_secret
from app.domains.audit.models import AuditEvent
from app.domains.source import service as route
from app.domains.source.models import SourceConnection
from app.domains.source.schemas import JiraConfig
from tests.authz.test_tenant_access import headers, tenant_data  # noqa: F401
from tests.connectors.test_mock_jira import jira  # noqa: F401

BODY = {
    "deployment": "cloud",
    "base_url": "http://testserver/demo-org/",
    "auth_type": "api_token",
    "username": "jira-admin@example.com",
    "secret": "mock-cloud-token",
}
SINCE = datetime(2000, 1, 1, tzinfo=UTC)


def make_connection(body: dict[str, Any]) -> SourceConnection:
    return SourceConnection(
        config=JiraConfig.model_validate(body).model_dump(mode="json"),
        secret_ciphertext=encrypt_secret(body["secret"]),
    )


@pytest.mark.parametrize("description", [False, True])
def test_adapters(request: pytest.FixtureRequest, description: bool) -> None:
    http = request.getfixturevalue("jira")
    calls: list[httpx.Request] = []
    http.event_hooks["request"].append(calls.append)
    results = []
    for update in (
        {},
        {"deployment": "data_center", "auth_type": "pat", "secret": "mock-dc-pat"},
        {
            "deployment": "data_center",
            "auth_type": "basic",
            "username": "jira-admin",
            "secret": "mock-password",
        },
    ):
        with jira_client_for(
            make_connection({**BODY, **update, "include_description": description}),
            http,
        ) as adapter:
            assert adapter.current_account() == "Jira Admin"
            rows, cursor = [], None
            while True:
                page = adapter.search(SINCE, cursor, 10)
                rows.extend(page.issues)
                cursor = page.next_cursor
                if cursor is None:
                    break
            assert len(rows) == len({r.key for r in rows}) == 37
            assert all(bool(row.description) == description for row in rows)
            assert all(row.url.endswith("/browse/" + row.key) for row in rows)
            assert 0 < len(adapter.search(rows[-1].updated, None).issues) < 37
            results.append(rows)
    assert [r.key for r in results[0]] == [r.key for r in results[1]]
    assert results[0][0].people[0].id != results[1][0].people[0].id
    assert results[1] == results[2]
    for call in calls:
        assert not call.url.path.endswith("/3/search")
        if "search" in call.url.path:
            fields = (
                json.loads(call.content)["fields"]
                if call.method == "POST"
                else call.url.params["fields"].split(",")
            )
            assert ("description" in fields) == description
            assert not {"comment", "worklog", "attachment", "changelog"} & set(fields)


def test_text() -> None:
    def node(kind: str, *children: dict[str, Any], **extra: Any) -> dict[str, Any]:
        return {"type": kind, "content": list(children), **extra}

    def text(value: str) -> dict[str, Any]:
        return node("text", text=value)

    document = node(
        "doc",
        node("heading", text("标题")),
        node(
            "paragraph",
            text("第一行"),
            node("hardBreak"),
            node("mention", attrs={"text": "@员工"}),
        ),
        node("bulletList", node("listItem", node("paragraph", text("列表")))),
        node("codeBlock", text("a_*\n b")),
    )
    assert adf_to_text(document) == "标题\n第一行\n@员工\n- 列表\na_*\n b\n"
    assert adf_to_text(None) == "" and wiki_to_text(None) == ""
    assert (
        wiki_to_text(
            "h2. 标题\n*粗体* _斜体_ [文字|https://example.com]\n{code:python}x{code}{noformat}y{noformat}{color:red}z{color}"
        )
        == "标题\n粗体 斜体 文字\nxyz"
    )


def test_code_description() -> None:
    original = "  indented_code()\n\tsecond_line()  \n"
    issue = {
        "key": "SYNTHETIC-1",
        "fields": {
            "summary": "Synthetic code",
            "created": SINCE.isoformat(),
            "updated": SINCE.isoformat(),
            "description": {
                "type": "codeBlock",
                "content": [{"type": "text", "text": original}],
            },
        },
    }
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"issues": [issue], "isLast": True})
        )
    ) as http:
        with jira_client_for(
            make_connection({**BODY, "include_description": True}), http
        ) as adapter:
            assert adapter.search(SINCE, None).issues[0].description == original


def test_settings(
    request: pytest.FixtureRequest,
    client: TestClient,
    db: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    orgs, _, _, users = request.getfixturevalue("tenant_data")
    url, auth = f"/api/v1/orgs/{orgs[0].id}/sources/jira", headers(users[0])
    assert client.get(url, headers=auth).json() == {"configured": False}
    assert not client.post(url + "/test", headers=auth).json()["ok"]
    assert client.put(url, headers=auth, json=BODY).status_code == 200
    stored = db.exec(
        select(SourceConnection).where(SourceConnection.org_id == orgs[0].id)
    ).one()
    token = stored.secret_ciphertext
    assert decrypt_secret(token) == BODY["secret"]
    body = {k: v for k, v in BODY.items() if k != "secret"}
    stored.synced_until = SINCE
    db.add(stored)
    db.commit()
    assert client.put(url, headers=auth, json=body).json()["synced_until"]
    db.refresh(stored)
    assert stored.secret_ciphertext == token
    audit_query = select(AuditEvent).where(AuditEvent.org_id == orgs[0].id)
    for change in (
        {"base_url": "https://other.example.com"},
        {"deployment": "data_center", "auth_type": "pat"},
        {"deployment": "data_center", "auth_type": "basic"},
    ):
        before, count = stored.model_dump(), len(db.exec(audit_query).all())
        for secret in ({}, {"secret": None}):
            denied = client.put(url, headers=auth, json={**body, **change, **secret})
            assert denied.status_code == 422 and "请重新填写凭据" in denied.text
            db.refresh(stored)
            assert (
                stored.model_dump() == before
                and len(db.exec(audit_query).all()) == count
            )
        assert (
            client.put(
                url, headers=auth, json={**body, **change, "secret": "new-token"}
            ).status_code
            == 200
        )
        db.refresh(stored)
        assert (
            stored.synced_until is None
            and decrypt_secret(stored.secret_ciphertext) == "new-token"
        )
        assert client.put(url, headers=auth, json=BODY).status_code == 200
        db.refresh(stored)
    event = db.exec(audit_query.order_by(AuditEvent.created_at.desc())).first()
    assert event and event.details == {
        "deployment": "cloud",
        "hostname": "testserver",
        "auth_type": "api_token",
        "secret_changed": True,
        "sync_schedule": "off",
    }
    public = client.get(url, headers=auth)
    assert public.json()["has_secret"] and "ciphertext" not in public.text
    assert BODY["secret"] not in public.text + caplog.text + str(event.details)
    for index, status in ((1, 403), (2, 403), (3, 404), (0, 404)):
        target = url if index else url.replace(str(orgs[0].id), str(orgs[1].id))
        for method, path, kwargs in (
            ("GET", target, {}),
            ("PUT", target, {"json": BODY}),
            ("POST", target + "/test", {}),
        ):
            assert (
                client.request(
                    method, path, headers=headers(users[index]), **kwargs
                ).status_code
                == status
            )


@pytest.mark.parametrize(
    "update",
    [
        {"base_url": "not a url"},
        {"base_url": "ftp://jira.example.com"},
        {"base_url": "https://user:password@jira.example.com"},
        {"base_url": "https://jira.example.com?token=x"},
        {"auth_type": "pat"},
        {"deployment": "data_center"},
        {"username": ""},
        {"secret": ""},
    ],
)
def test_validation(
    update: dict[str, Any],
    request: pytest.FixtureRequest,
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    orgs, _, _, users = request.getfixturevalue("tenant_data")
    response = client.put(
        f"/api/v1/orgs/{orgs[0].id}/sources/jira",
        headers=headers(users[0]),
        json={**BODY, **update},
    )
    assert (
        response.status_code == 422
        and BODY["secret"] not in response.text + caplog.text
    )


@pytest.mark.parametrize(
    "outcome,message",
    [
        (200, "Jira 连接成功"),
        (401, "账号或令牌不正确"),
        (403, "账号没有浏览或搜索 issue 的权限"),
        (404, "地址不对，或者 Jira 类型选错了"),
        (410, "地址不对，或者 Jira 类型选错了"),
        (429, "Jira 暂时不可用"),
        (503, "Jira 暂时不可用"),
        (302, "Jira 请求失败"),
        ("network", NETWORK_MESSAGE),
        ("cert", "站点的 HTTPS 证书无法验证"),
        ("timeout", NETWORK_MESSAGE),
        ("read", NETWORK_MESSAGE),
        ("secret", "保存的密钥已失效，请重新填写"),
    ],
)
def test_connection(
    outcome: int | str,
    message: str,
    request: pytest.FixtureRequest,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    orgs, _, _, users = request.getfixturevalue("tenant_data")
    url, auth = f"/api/v1/orgs/{orgs[0].id}/sources/jira", headers(users[0])
    assert client.put(url, headers=auth, json=BODY).status_code == 200
    calls = []

    def respond(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        assert req.extensions["timeout"]["connect"] == 10
        assert req.extensions["timeout"]["read"] == 30
        if outcome == 403 and req.url.path.endswith("myself"):
            return httpx.Response(200, json={"displayName": "Synthetic Admin"})
        if outcome in ("network", "cert"):
            raise httpx.ConnectError(
                "CERTIFICATE_VERIFY_FAILED" if outcome == "cert" else "private-token"
            )
        if outcome in ("timeout", "read"):
            raise (httpx.ConnectTimeout if outcome == "timeout" else httpx.ReadTimeout)(
                "private-token"
            )
        status = outcome if isinstance(outcome, int) else 200
        return httpx.Response(
            status,
            headers={"Retry-After": "12", "Location": "https://other.example.com"},
            json={"displayName": "Synthetic Admin"}
            if req.url.path.endswith("myself")
            else {"issues": [], "isLast": True},
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        if outcome == 429:
            with pytest.raises(RetryableSourceError) as caught:
                jira_client_for(make_connection(BODY), http).current_account()
            assert caught.value.retry_after == 12
            calls.clear()
        monkeypatch.setattr(
            route, "jira_client_for", lambda conn: jira_client_for(conn, http)
        )
        if outcome == "secret":
            monkeypatch.setattr(settings, "SECRET_KEY", "rotated-key")
            auth = headers(users[0])
        response = client.post(url + "/test", headers=auth)
    result = response.json()
    assert response.status_code == 200 and message in result["message"]
    assert result["ok"] == (outcome == 200)
    assert result["account_name"] == ("Synthetic Admin" if outcome == 200 else None)
    if outcome == 200:
        assert len(calls) == 2 and json.loads(calls[1].content)["maxResults"] == 1
    else:
        assert len(calls) <= (2 if outcome == 403 else 1)
    assert BODY["secret"] not in response.text + caplog.text
