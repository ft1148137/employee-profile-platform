"""Read-only, opt-in Cloud contract checks. Never run with --showlocals/-l."""

import logging
import os
import re
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.api.routes import sources
from app.authz.principal import Principal
from app.connectors.jira.client import jira_client_for
from app.connectors.jira.mock import create_mock_app
from app.core.secrets import encrypt_secret
from app.domains.source.models import SourceConnection
from tests.connectors.test_mock_jira import EXAMPLES
from tests.connectors.test_seed_jira_cloud import SCRIPT

pytestmark = pytest.mark.skipif(
    any(
        not os.getenv(k)
        for k in (
            "JIRA_DEPLOYMENT",
            "JIRA_BASE_URL",
            "JIRA_EMAIL",
            "JIRA_API_TOKEN",
            "JIRA_PROJECT_KEY",
        )
    )
    or os.getenv("JIRA_DEPLOYMENT") != "cloud",
    reason="没有真实 Jira Cloud 测试站点的凭据",
)
FIELDS = [
    "summary",
    "issuetype",
    "status",
    "assignee",
    "reporter",
    "created",
    "updated",
    "resolutiondate",
    "labels",
    "components",
    "description",
]


@pytest.fixture
def live(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[tuple[SourceConnection, httpx.Client, list[str]]]:
    # HTTP logs can contain the private hostname even when assertions contain no values.
    for name in ("httpx", "httpcore"):
        monkeypatch.setattr(logging.getLogger(name), "disabled", True)
    paths: list[str] = []
    connection = SourceConnection(
        org_id=uuid4(),
        config={
            "deployment": "cloud",
            "auth_type": "api_token",
            "base_url": os.environ["JIRA_BASE_URL"],
            "username": os.environ["JIRA_EMAIL"],
            "start_date": "2000-01-01",
            "include_description": True,
        },
        secret_ciphertext=encrypt_secret(os.environ["JIRA_API_TOKEN"]),
    )
    with httpx.Client(
        base_url=os.environ["JIRA_BASE_URL"],
        auth=(os.environ["JIRA_EMAIL"], os.environ["JIRA_API_TOKEN"]),
        timeout=30,
        event_hooks={"request": [lambda r: paths.append(r.url.path)]},
    ) as http:
        try:
            yield connection, http, paths
        except httpx.RequestError:
            raise AssertionError("Live Jira network failure") from None
    assert all(not p.endswith("/3/search") for p in paths)


def read(
    http: httpx.Client, path: str, body: dict[str, Any] | None = None
) -> dict[str, Any]:
    response = http.request(
        "GET" if body is None else "POST", "/rest/api/3/" + path, json=body
    )
    if response.status_code != 200:
        raise AssertionError(f"Live Jira HTTP {response.status_code}")
    return response.json()


def test_connection(
    live: tuple[SourceConnection, httpx.Client, list[str]],
    monkeypatch: pytest.MonkeyPatch,
    db: Session,
) -> None:
    connection, http, _ = live
    account = read(http, "myself")
    monkeypatch.setattr(sources, "read_jira", lambda *_: connection)
    monkeypatch.setattr(
        sources, "jira_client_for", lambda conn: jira_client_for(conn, http)
    )
    principal = Principal.system_worker(connection.org_id)
    result = sources.test_jira(db, principal)
    assert result.ok and bool(result.account_name == account["displayName"])
    connection.secret_ciphertext = encrypt_secret("deliberately-invalid-token")
    result = sources.test_jira(db, principal)
    assert not result.ok and result.message == "账号或令牌不正确"


def test_pagination_text_and_people(
    live: tuple[SourceConnection, httpx.Client, list[str]],
) -> None:
    connection, http, paths = live
    account = read(http, "myself")["accountId"]
    rows, cursors, cursor = [], set(), None
    with jira_client_for(connection, http) as adapter:
        while True:
            page = adapter.search(datetime(2000, 1, 1, tzinfo=UTC), cursor, 10)
            rows.extend(page.issues)
            cursor = page.next_cursor
            if cursor is None:
                break
            assert cursor not in cursors
            cursors.add(cursor)
        assert not adapter.search(datetime.now(UTC) + timedelta(days=365), None).issues
    assert len(rows) >= 60 and len(rows) == len({r.key for r in rows})
    assert len(cursors) + 1 >= 6
    expected = {
        "epp-src-" + r["key"].lower(): r["fields"]["description"]
        for i, r in enumerate(SCRIPT["seed_rows"]())
        if i % 2 == 0
    }
    described, assigned, seeds = 0, 0, 0
    for row in rows:
        if "epp-seed" not in row.labels:
            continue
        seeds += 1
        for label in row.labels:
            if label in expected:
                assert row.description == expected[label]
                described += 1
        assignees = [p for p in row.people if p.role == "assignee"]
        if assignees:
            assert bool(assignees[0].id == account)
            assigned += 1
    assert seeds >= 60 and described == 30 and assigned == 40
    assert sum(p.endswith("/search/jql") for p in paths) >= 7


def shape(value: Any, path: str = "") -> set[str]:
    result = {path + ":" + type(value).__name__}
    if isinstance(value, dict):
        for key, child in value.items():
            result |= shape(child, path + "." + key)
    elif isinstance(value, list):
        for child in value:
            result |= shape(child, path + "[]")
    return result


def same_shape(left: set[str], right: set[str]) -> None:
    if left != right:
        raise AssertionError(
            "Structure differs (keys/types only): " + repr(sorted(left ^ right))
        )


def test_mock_contract(
    live: tuple[SourceConnection, httpx.Client, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, http, _ = live
    for key in ("EMAIL", "API_TOKEN", "PAT", "USERNAME", "PASSWORD"):
        monkeypatch.delenv("MOCK_JIRA_" + key, raising=False)
    with TestClient(create_mock_app(EXAMPLES)) as mock:
        real_body: dict[str, Any] = {
            "jql": "project = SCRUM AND labels = epp-seed ORDER BY updated ASC, key ASC",
            "fields": FIELDS,
            "maxResults": 10,
        }
        mock_body = {**real_body, "jql": "ORDER BY updated ASC, key ASC"}
        pages = []
        for source, body in ((http, real_body), (mock, mock_body)):
            collected = []
            while True:
                page = (
                    read(http, "search/jql", body)
                    if source is http
                    else mock.post(
                        "/demo-org/rest/api/3/search/jql",
                        json=body,
                        auth=("jira-admin@example.com", "mock-cloud-token"),
                    ).json()
                )
                collected.append(page)
                if page["isLast"]:
                    break
                body["nextPageToken"] = page["nextPageToken"]
            pages.append(collected)
        for index in (0, -1):
            same_shape(
                shape({k: v for k, v in pages[0][index].items() if k != "issues"}),
                shape({k: v for k, v in pages[1][index].items() if k != "issues"}),
            )
        datasets = [
            [row for page in group for row in page["issues"]] for group in pages
        ]
        same_shape(
            shape({k: v for k, v in datasets[0][0].items() if k != "fields"}),
            shape({k: v for k, v in datasets[1][0].items() if k != "fields"}),
        )
        for field in FIELDS:
            values = [[r["fields"][field] for r in rows] for rows in datasets]
            # This team-managed site cannot expose a populated component object.
            if field == "components":
                assert all(isinstance(v, list) for group in values for v in group)
                continue
            same_shape(
                set().union(*(shape(v) for v in values[0] if v is not None)),
                set().union(*(shape(v) for v in values[1] if v is not None)),
            )
            if field in ("created", "updated", "resolutiondate"):
                assert all(
                    re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}[+-]\d{4}", v)
                    for group in values
                    for v in group
                    if v is not None
                )
