import json
import runpy
from copy import deepcopy
from pathlib import Path
from typing import Any

import httpx
import pytest

SCRIPT = runpy.run_path(
    str(Path(__file__).resolve().parents[3] / "scripts/seed_jira_cloud.py")
)
Seeder, SeedError = SCRIPT["Seeder"], SCRIPT["SeedError"]


@pytest.mark.parametrize(
    "project,deployment", [("OTHER", "Cloud"), ("SCRUM", "Server")]
)
def test_refuses_wrong_destination(project: str, deployment: str) -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(200, json={"deploymentType": deployment})

    with httpx.Client(
        base_url="https://jira.example.com", transport=httpx.MockTransport(respond)
    ) as http:
        with pytest.raises(SeedError):
            Seeder(http, project).run()
    assert calls == ([] if project != "SCRUM" else ["GET"])


def test_idempotence_touch_and_label_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = SCRIPT["seed_rows"]()[:3]
    monkeypatch.setitem(Seeder.run.__globals__, "seed_rows", lambda: rows)
    stored: dict[str, Any] = {}
    writes = []

    def respond(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/rest/api/3/")
        body = json.loads(request.content) if request.content else {}
        if request.method in ("PUT", "POST") and path != "search/jql":
            writes.append(path)
        if path == "serverInfo":
            data: Any = {"deploymentType": "Cloud"}
        elif path == "myself":
            data = {"accountId": "synthetic-user"}
        elif path == "project/SCRUM":
            data = {"style": "next-gen"}
        elif path == "search/jql":
            assert body["jql"] == "project = SCRUM AND labels = epp-seed"
            data = {
                "issues": [{"key": k, "fields": v} for k, v in stored.items()],
                "isLast": True,
            }
        elif path == "issue":
            key = f"SCRUM-{len(stored) + 1}"
            stored[key] = deepcopy(body["fields"])
            stored[key]["status"] = {"statusCategory": {"key": "new"}}
            data = {"key": key}
        else:
            key = path.split("/")[1]
            if path.endswith("transitions"):
                if request.method == "GET":
                    data = {
                        "transitions": [
                            {"id": c, "to": {"statusCategory": {"key": c}}}
                            for c in ("new", "done", "indeterminate")
                        ]
                    }
                else:
                    stored[key]["status"]["statusCategory"]["key"] = body["transition"][
                        "id"
                    ]
                    data = {}
            elif request.method == "GET":
                data = {"fields": stored[key]}
            else:
                stored[key].update(body["fields"])
                data = {}
        assert request.method != "DELETE"
        return httpx.Response(200, json=data)

    with httpx.Client(
        base_url="https://jira.example.com", transport=httpx.MockTransport(respond)
    ) as http:
        seeder = Seeder(http, "SCRUM")
        assert seeder.run() == {"created": 3, "updated": 0, "unchanged": 0}
        writes.clear()
        assert seeder.run() == {"created": 0, "updated": 0, "unchanged": 3}
        assert writes == []
        assert seeder.run(rows[0]["key"])["updated"] == 1
        assert stored["SCRUM-1"]["summary"].endswith("（已更新）")
        assert seeder.run()["updated"] == 1
        assert stored["SCRUM-1"]["summary"] == rows[0]["fields"]["summary"]
        stored["SCRUM-1"]["labels"].remove("epp-seed")
        with pytest.raises(SeedError, match="Refusing"):
            seeder.guarded_issue("SCRUM-1")


def test_retry_and_safe_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    delays = []
    monkeypatch.setattr(SCRIPT["time"], "sleep", delays.append)
    monkeypatch.setenv("JIRA_API_TOKEN", "private-token")
    responses = [
        httpx.Response(429, headers={"Retry-After": "2"}),
        httpx.Response(
            400,
            json={"errorMessages": ["private-token"], "errors": {"private": "hidden"}},
        ),
    ]
    with httpx.Client(
        base_url="https://jira.example.com",
        transport=httpx.MockTransport(lambda _: responses.pop(0)),
    ) as http:
        with pytest.raises(SeedError, match=r"HTTP 400") as caught:
            Seeder(http, "SCRUM").request("GET", "serverInfo")
    assert delays == [2]
    assert "private-token" not in str(caught.value) and "hidden" not in str(
        caught.value
    )
