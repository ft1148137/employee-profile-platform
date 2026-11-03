"""Seed synthetic SCRUM issues; Jira assigns creation/resolution times at execution.

Load the five JIRA_* variables from the local credential file before running:
uv run --project backend python scripts/seed_jira_cloud.py [--touch PLAT-101]
No issues are deleted; only issues labelled epp-seed may be edited.
"""

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
FIELDS = [
    "summary",
    "issuetype",
    "labels",
    "description",
    "assignee",
    "components",
    "status",
    "project",
]


class SeedError(Exception):
    pass


def seed_rows() -> list[dict[str, Any]]:
    demo = json.loads((ROOT / "examples/demo-org/jira/issues.json").read_text())[
        "issues"
    ]
    extra = json.loads(
        (ROOT / "examples/test-enterprises/qingli-saas/jira/issues.json").read_text()
    )["issues"]
    return demo + extra[:23]


class Seeder:
    def __init__(self, http: httpx.Client, project: str) -> None:
        self.http = http
        self.project = project

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        while True:
            response = self.http.request(method, "/rest/api/3/" + path, **kwargs)
            if response.status_code != 429:
                break
            retry = response.headers.get("Retry-After", "1")
            try:
                delay = float(retry)
            except ValueError:
                delay = (
                    parsedate_to_datetime(retry) - datetime.now(UTC)
                ).total_seconds()
            time.sleep(max(0, delay))
        if not response.is_success:
            try:
                messages = response.json().get("errorMessages", [])
            except ValueError:
                messages = []
            message = json.dumps(messages, ensure_ascii=False)
            for name in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"):
                if os.getenv(name):
                    message = message.replace(os.environ[name], "[redacted]")
            raise SeedError(f"HTTP {response.status_code}: {message}")
        return response.json() if response.content else {}

    def guarded_issue(self, key: str) -> dict[str, Any]:
        issue = self.request("GET", f"issue/{key}", params={"fields": ",".join(FIELDS)})
        fields = issue["fields"]
        if fields["project"]["key"] != "SCRUM" or "epp-seed" not in fields["labels"]:
            raise SeedError("Refusing to edit an issue outside labelled SCRUM seeds")
        return fields

    def run(self, touch: str | None = None) -> dict[str, int]:
        if self.project != "SCRUM":
            raise SeedError("Only SCRUM is allowed")
        if self.request("GET", "serverInfo")["deploymentType"] != "Cloud":
            raise SeedError("Only Jira Cloud is allowed")
        rows = seed_rows()
        if touch and touch not in {row["key"] for row in rows}:
            raise SeedError("Unknown source key")
        account = self.request("GET", "myself")["accountId"]
        project = self.request("GET", "project/SCRUM")
        components = None
        if project.get("style") != "next-gen":
            components = {
                c["name"]: c["id"]
                for c in self.request("GET", "project/SCRUM/components")
            }
        else:
            print("Components unsupported by team-managed project; skipped")
        existing: dict[str, str] = {}
        body: dict[str, Any] = {
            "jql": "project = SCRUM AND labels = epp-seed",
            "fields": ["labels"],
            "maxResults": 100,
        }
        while True:
            page = self.request("POST", "search/jql", json=body)
            for issue in page["issues"]:
                for label in issue["fields"]["labels"]:
                    if label.startswith("epp-src-"):
                        if label in existing:
                            raise SeedError(
                                "Duplicate source labels; refusing ambiguous updates"
                            )
                        existing[label] = issue["key"]
            if page["isLast"]:
                break
            body["nextPageToken"] = page["nextPageToken"]
        counts = {"created": 0, "updated": 0, "unchanged": 0}
        for index, row in enumerate(rows):
            source = row["fields"]
            label = "epp-src-" + row["key"].lower()
            desired: dict[str, Any] = {
                "summary": source["summary"]
                + ("（已更新）" if row["key"] == touch else ""),
                "issuetype": {
                    "name": "Story"
                    if source["issuetype"]["name"] == "Story"
                    else "Task"
                },
                "labels": sorted(set(source["labels"]) | {"epp-seed", label}),
                "assignee": {"accountId": account} if index % 3 != 2 else None,
                "description": {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [
                                {"type": "text", "text": source["description"]}
                            ],
                        }
                    ],
                }
                if index % 2 == 0
                else None,
            }
            if components is not None:
                for component in source["components"]:
                    name = component["name"]
                    if name not in components:
                        components[name] = self.request(
                            "POST", "component", json={"project": "SCRUM", "name": name}
                        )["id"]
                desired["components"] = [
                    {"id": components[c["name"]]} for c in source["components"]
                ]
            key = existing.get(label)
            category = {"Done": "done", "In Progress": "indeterminate"}.get(
                source["status"]["name"], "new"
            )
            if key is None:
                key = self.request(
                    "POST",
                    "issue",
                    json={"fields": {"project": {"key": "SCRUM"}, **desired}},
                )["key"]
                outcome = "created"
                current = self.guarded_issue(key)
            else:
                current = self.guarded_issue(key)
                changes = {}
                for field, value in desired.items():
                    actual = current.get(field)
                    if field in ("issuetype", "assignee") and actual is not None:
                        part = "name" if field == "issuetype" else "accountId"
                        actual = {part: actual[part]}
                    if field == "components":
                        actual = [{"id": c["id"]} for c in actual or []]
                    if field in ("labels", "components"):
                        actual = sorted(actual or [], key=str)
                        value = sorted(value, key=str)
                    if actual != value:
                        changes[field] = value
                outcome = "unchanged"
                if changes:
                    self.request("PUT", f"issue/{key}", json={"fields": changes})
                    outcome = "updated"
            if current["status"]["statusCategory"]["key"] != category:
                transitions = self.request("GET", f"issue/{key}/transitions")[
                    "transitions"
                ]
                transition = next(
                    (
                        t
                        for t in transitions
                        if t["to"]["statusCategory"]["key"] == category
                    ),
                    None,
                )
                if transition is None:
                    raise SeedError("No transition to requested status category")
                self.guarded_issue(key)
                self.request(
                    "POST",
                    f"issue/{key}/transitions",
                    json={"transition": {"id": transition["id"]}},
                )
                if outcome == "unchanged":
                    outcome = "updated"
            counts[outcome] += 1
        return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--touch")
    args = parser.parse_args()
    try:
        if os.environ.get("JIRA_DEPLOYMENT") != "cloud":
            raise SeedError("JIRA_DEPLOYMENT must be cloud")
        with httpx.Client(
            base_url=os.environ["JIRA_BASE_URL"].rstrip("/"),
            auth=(os.environ["JIRA_EMAIL"], os.environ["JIRA_API_TOKEN"]),
            timeout=30,
            follow_redirects=False,
        ) as http:
            print(Seeder(http, os.environ["JIRA_PROJECT_KEY"]).run(args.touch))
    except SeedError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (httpx.RequestError, KeyError, ValueError):
        print("Invalid configuration, response or network failure", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
