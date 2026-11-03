import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.connectors.jira.mock import create_mock_app

EXAMPLES = Path(__file__).resolve().parents[3] / "examples"
CLOUD = {"Authorization": "Basic amlyYS1hZG1pbkBleGFtcGxlLmNvbTptb2NrLWNsb3VkLXRva2Vu"}
DC = {"Authorization": "Bearer mock-dc-pat"}


@pytest.fixture
def jira(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    for key in ("EMAIL", "API_TOKEN", "PAT", "USERNAME", "PASSWORD"):
        monkeypatch.delenv("MOCK_JIRA_" + key, raising=False)
    shutil.copytree(EXAMPLES, tmp_path / "examples")
    return TestClient(create_mock_app(tmp_path / "examples"))


@pytest.mark.parametrize("version,headers", [("3", CLOUD), ("2", DC)])
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_pagination(
    jira: TestClient, version: str, headers: dict[str, str], method: str
) -> None:
    path = f"/demo-org/rest/api/{version}/search" + ("/jql" if version == "3" else "")
    params: dict[str, Any] = {"maxResults": 10}
    rows = []
    while True:
        response = jira.request(
            method,
            path,
            headers=headers,
            **({"params": params} if method == "GET" else {"json": params}),
        )
        assert response.status_code == 200
        page = response.json()
        rows.extend(page["issues"])
        if version == "3":
            assert "total" not in page
            if page["isLast"]:
                assert "nextPageToken" not in page
                break
            params["nextPageToken"] = page["nextPageToken"]
        else:
            assert page["startAt"] == len(rows) - len(page["issues"])
            assert page["maxResults"] == 10 and page["total"] == 37
            if len(rows) == page["total"]:
                break
            params["startAt"] = len(rows)
    assert len(rows) == len({row["key"] for row in rows}) == 37
    assert rows == sorted(rows, key=lambda row: (row["fields"]["updated"], row["key"]))
    page = jira.get(path, headers=headers, params={"maxResults": 200}).json()
    assert len(page["issues"]) == 37
    if version == "2":
        assert page["maxResults"] == 100


def test_formats_and_users(jira: TestClient) -> None:
    cloud = jira.get("/demo-org/rest/api/3/search/jql", headers=CLOUD).json()["issues"]
    dc = jira.get("/demo-org/rest/api/2/search", headers=DC).json()["issues"]
    assert [row["key"] for row in cloud] == [row["key"] for row in dc]
    assert cloud[0]["id"].isdigit() and "/rest/api/3/issue/" in cloud[0]["self"]
    for c, d in zip(cloud, dc, strict=True):
        assert c["fields"]["description"]["type"] == "doc"
        assert c["fields"]["description"]["version"] == 1
        assert isinstance(d["fields"]["description"], str)
        for role in ("assignee", "reporter"):
            user = c["fields"][role]
            other = d["fields"][role]
            assert "accountId" not in other and "key" not in user
            assert other["key"].startswith("JIRAUSER")
            assert other["key"] != user["accountId"]
            assert other["displayName"] == user["displayName"]
    user = next(
        row["fields"]["assignee"]
        for row in cloud
        if row["fields"]["assignee"]["accountId"] == "demo-jira-01"
    )
    assert user == {
        "accountId": "demo-jira-01",
        "displayName": "林澈",
        "emailAddress": "employee01@demo.example.com",
        "active": True,
        "self": "https://mock.example.com/rest/api/3/user?accountId=demo-jira-01",
        "accountType": "atlassian",
        "timeZone": "Asia/Shanghai",
        "avatarUrls": {
            f"{size}x{size}": "https://mock.example.com/avatar"
            for size in (16, 24, 32, 48)
        },
    }
    assert all(row["fields"]["updated"].endswith(".000+0800") for row in cloud)


@pytest.mark.parametrize(
    "dataset,count", [("demo-org", 37), ("qingli-saas", 38), ("qiguang-aiot", 36)]
)
def test_datasets(jira: TestClient, dataset: str, count: int) -> None:
    response = jira.get(f"/{dataset}/rest/api/2/search", headers=DC)
    assert response.status_code == 200 and response.json()["total"] == count


@pytest.mark.parametrize(
    "method,fields",
    [
        ("GET", "summary,assignee"),
        ("POST", ["summary", "assignee"]),
        ("POST", "summary,assignee"),
        ("GET", ["summary", "assignee"]),
    ],
)
def test_fields(jira: TestClient, method: str, fields: Any) -> None:
    response = jira.request(
        method,
        "/demo-org/rest/api/3/search/jql",
        headers=CLOUD,
        **(
            {"params": {"fields": fields}}
            if method == "GET"
            else {"json": {"fields": fields}}
        ),
    )
    assert response.status_code == 200
    assert all(
        set(row["fields"]) == {"summary", "assignee"}
        for row in response.json()["issues"]
    )


def test_modified_dataset_and_timezone(tmp_path: Path) -> None:
    directory = tmp_path / "demo-org"
    shutil.copytree(EXAMPLES / "demo-org", directory)
    path = directory / "jira/issues.json"
    rows = json.loads(path.read_text())["issues"][:3]
    for row, date in zip(
        rows,
        [
            "2026-04-01T15:59:59.000+0000",
            "2026-04-01T16:00:00.000+0000",
            "2026-04-02T01:00:00.000+0800",
        ],
        strict=True,
    ):
        row["fields"]["updated"] = date
        row["fields"]["assignee"] = {
            "accountId": "outside-org",
            "displayName": "外部账号",
        }
        row["fields"]["description"] = "第一段\n\n第二段"
    rows[0]["fields"]["resolutiondate"] = None
    rows[0]["fields"].pop("updated")
    rows[0]["fields"]["created"] = "2026-04-01T15:59:59.000+0000"
    path.write_text(json.dumps({"issues": rows}))
    client = TestClient(create_mock_app(tmp_path))
    for date in ("2026-04-02 00:00", "2026/04/02 00:00", "2026-04-02"):
        page = client.get(
            "/demo-org/rest/api/3/search/jql",
            headers=CLOUD,
            params={
                "jql": f'project in (PLAT, OTHER) AND updated >= "{date}" ORDER BY updated ASC, key ASC'
            },
        ).json()
        assert [row["key"] for row in page["issues"]] == [
            rows[1]["key"],
            rows[2]["key"],
        ]
        assert page["issues"][0]["fields"]["assignee"] == {
            "accountId": "outside-org",
            "displayName": "外部账号",
            "active": True,
            "self": "https://mock.example.com/rest/api/3/user?accountId=outside-org",
            "accountType": "atlassian",
            "timeZone": "Asia/Shanghai",
            "avatarUrls": {
                f"{size}x{size}": "https://mock.example.com/avatar"
                for size in (16, 24, 32, 48)
            },
        }
        assert len(page["issues"][0]["fields"]["description"]["content"]) == 2
    for app in (client, TestClient(create_mock_app(tmp_path))):
        result = app.get(
            "/demo-org/rest/api/2/search",
            headers=DC,
            params={"jql": "project = PLAT ORDER BY updated ASC"},
        ).json()
        assert result["total"] == 3
        assert (
            result["issues"][0]["fields"]["assignee"]["key"]
            == client.get("/demo-org/rest/api/2/search", headers=DC).json()["issues"][
                0
            ]["fields"]["assignee"]["key"]
        )
    path.write_text('{"issues": []}')
    assert (
        len(
            client.get("/demo-org/rest/api/3/search/jql", headers=CLOUD).json()[
                "issues"
            ]
        )
        == 3
    )
    assert (
        TestClient(create_mock_app(tmp_path))
        .get("/demo-org/rest/api/2/search", headers=DC)
        .json()["total"]
        == 0
    )


@pytest.mark.parametrize(
    "jql",
    [
        "status = Done",
        "project = PLAT OR project = EDGE",
        'updated >= "2026-99-99"',
        'updated >= "2026/04/02"',
        "ORDER BY updated DESC",
        "ORDER BY updated ASC ORDER BY key ASC",
        "project in ()",
        "project = PLAT AND",
    ],
)
def test_invalid_jql(jira: TestClient, jql: str) -> None:
    response = jira.get(
        "/demo-org/rest/api/3/search/jql", headers=CLOUD, params={"jql": jql}
    )
    assert response.status_code == 400 and response.json()["errorMessages"]


@pytest.mark.parametrize(
    "params",
    [
        {"nextPageToken": "bad"},
        {"nextPageToken": "-1"},
        {"maxResults": 0},
        {"maxResults": "bad"},
        {"startAt": -1},
    ],
)
def test_invalid_params(jira: TestClient, params: dict[str, Any]) -> None:
    response = jira.get("/demo-org/rest/api/3/search/jql", headers=CLOUD, params=params)
    assert response.status_code == 400 and response.json()["errorMessages"]


def test_auth_errors_and_health(
    jira: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for path, headers in [
        ("3/myself", CLOUD),
        ("3/search/jql", CLOUD),
        ("2/myself", DC),
        ("2/search", DC),
    ]:
        assert (
            jira.get("/demo-org/rest/api/" + path, headers=headers).status_code == 200
        )
        for invalid in (
            {},
            {"Authorization": "Bearer wrong"},
            DC if path.startswith("3") else CLOUD,
        ):
            assert (
                jira.get("/demo-org/rest/api/" + path, headers=invalid).status_code
                == 401
            )
    assert (
        jira.get(
            "/demo-org/rest/api/2/myself", auth=("jira-admin", "mock-password")
        ).json()["name"]
        == "jira-admin"
    )
    assert jira.get("/unknown/rest/api/3/myself", headers=CLOUD).status_code == 404
    assert jira.get("/health").json() == {"status": "ok"}
    for method in ("GET", "POST"):
        assert (
            jira.request(
                method, "/demo-org/rest/api/3/search", headers=CLOUD
            ).status_code
            == 410
        )
    assert (
        jira.post(
            "/demo-org/rest/api/3/search/jql", headers=CLOUD, content="not json"
        ).status_code
        == 400
    )
    monkeypatch.setenv("MOCK_JIRA_API_TOKEN", "custom-token")
    custom = TestClient(create_mock_app(tmp_path / "examples"))
    assert custom.get("/demo-org/rest/api/3/myself", headers=CLOUD).status_code == 401
    assert (
        custom.get(
            "/demo-org/rest/api/3/myself",
            auth=("jira-admin@example.com", "custom-token"),
        ).status_code
        == 200
    )


def test_cloud_page_size_limit(tmp_path: Path) -> None:
    directory = tmp_path / "demo-org"
    shutil.copytree(EXAMPLES / "demo-org", directory)
    path = directory / "jira/issues.json"
    row = json.loads(path.read_text())["issues"][0]
    path.write_text(
        json.dumps({"issues": [dict(row, key=f"PLAT-{index}") for index in range(150)]})
    )
    client = TestClient(create_mock_app(tmp_path))
    page = client.get(
        "/demo-org/rest/api/3/search/jql", headers=CLOUD, params={"maxResults": 1000}
    ).json()
    assert len(page["issues"]) == 100 and page["isLast"] is False
    assert (
        len(
            client.get(
                "/demo-org/rest/api/3/search/jql",
                headers=CLOUD,
                params={"nextPageToken": page["nextPageToken"], "maxResults": 1000},
            ).json()["issues"]
        )
        == 50
    )
