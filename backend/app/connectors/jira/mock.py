"""Dataset-backed Jira endpoints for demos and connector integration tests."""

import base64
import hashlib
import json
import os
import re
import secrets
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import yaml
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

SHANGHAI = ZoneInfo("Asia/Shanghai")


class MockJiraError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        self.message = message
        self.status = status


class Search(BaseModel):
    jql: str = ""
    nextPageToken: str | None = None
    startAt: int = Field(default=0, ge=0)
    maxResults: int = Field(default=50, ge=1)
    fields: list[str] | str | None = None


def jira_date(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=SHANGHAI) if parsed.tzinfo is None else parsed


def user_object(user: dict[str, Any], version: str) -> dict[str, Any]:
    account = user["accountId"]
    result = {"displayName": user["displayName"], "active": True}
    if "emailAddress" in user:
        result["emailAddress"] = user["emailAddress"]
    if version == "3":
        result["accountId"] = account
        result.update(
            self=f"https://mock.example.com/rest/api/3/user?accountId={account}",
            accountType="atlassian",
            timeZone="Asia/Shanghai",
            avatarUrls={
                f"{size}x{size}": "https://mock.example.com/avatar"
                for size in (16, 24, 32, 48)
            },
        )
    else:
        result["key"] = "JIRAUSER" + str(
            int(hashlib.sha256(account.encode()).hexdigest()[:12], 16)
        )
        result["name"] = user.get("emailAddress", account).split("@")[0]
    return result


def filter_issues(issues: list[dict[str, Any]], jql: str) -> list[dict[str, Any]]:
    parts = re.split(r"\bORDER\s+BY\b", jql.strip(), flags=re.I)
    if len(parts) > 2 or (
        len(parts) == 2
        and not re.fullmatch(
            r"\s*updated\s+ASC(?:\s*,\s*key\s+ASC)?\s*", parts[1], re.I
        )
    ):
        raise MockJiraError("Unsupported ORDER BY")
    result = issues
    if parts[0].strip():
        for clause in re.split(r"\s+AND\s+", parts[0].strip(), flags=re.I):
            project = re.fullmatch(r"project\s*=\s*([\w-]+)", clause, re.I)
            projects = re.fullmatch(
                r'project\s+in\s*\(\s*((?:"[\w-]+"|[\w-]+)(?:\s*,\s*(?:"[\w-]+"|[\w-]+))*)\s*\)',
                clause,
                re.I,
            )
            updated = re.fullmatch(
                r'updated\s*>=\s*"(\d{4}[-/]\d{2}[-/]\d{2}(?: \d{2}:\d{2})?)"',
                clause,
                re.I,
            )
            if project or projects:
                matched = project or projects
                assert matched is not None
                keys = {key.strip().strip('"') for key in matched[1].split(",")}
                result = [
                    issue for issue in result if issue["key"].rsplit("-", 1)[0] in keys
                ]
            elif updated:
                value = updated[1]
                formats = ("%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M", "%Y-%m-%d")
                for fmt in formats:
                    try:
                        threshold = datetime.strptime(value, fmt).replace(
                            tzinfo=SHANGHAI
                        )
                        break
                    except ValueError:
                        continue
                else:
                    raise MockJiraError("Invalid JQL date")
                result = [
                    issue
                    for issue in result
                    if jira_date(issue["fields"]["updated"]) >= threshold
                ]
            else:
                raise MockJiraError("Unsupported JQL clause")
    return result


def create_mock_app(examples_dir: Path) -> FastAPI:
    datasets: dict[str, list[dict[str, Any]]] = {}
    paths = sorted(examples_dir.glob("*/jira/issues.json")) + sorted(
        examples_dir.glob("test-enterprises/*/jira/issues.json")
    )
    for path in paths:
        directory = path.parent.parent
        org = yaml.safe_load((directory / "org.yaml").read_text(encoding="utf-8"))
        users = {
            employee["account_id"]: {
                "accountId": employee["account_id"],
                "displayName": employee["display_name"],
                "emailAddress": employee["work_email"],
            }
            for employee in org["employees"]
            if employee.get("account_id")
        }
        issues = json.loads(path.read_text(encoding="utf-8"))["issues"]
        for issue in issues:
            issue["id"] = str(
                int(hashlib.sha256(issue["key"].encode()).hexdigest()[:12], 16)
            )
            fields = issue["fields"]
            fields["updated"] = (
                fields.get("updated")
                or fields.get("resolutiondate")
                or fields["created"]
            )
            for name in ("created", "updated", "resolutiondate"):
                if fields.get(name):
                    fields[name] = (
                        jira_date(fields[name])
                        .astimezone(SHANGHAI)
                        .strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
                        + "+0800"
                    )
            for role in ("assignee", "reporter"):
                if fields.get(role):
                    fields[role] = users.get(fields[role]["accountId"], fields[role])
        datasets[directory.name] = sorted(
            issues,
            key=lambda issue: (jira_date(issue["fields"]["updated"]), issue["key"]),
        )

    credentials = {
        key: os.getenv("MOCK_JIRA_" + key, default)
        for key, default in {
            "EMAIL": "jira-admin@example.com",
            "API_TOKEN": "mock-cloud-token",
            "PAT": "mock-dc-pat",
            "USERNAME": "jira-admin",
            "PASSWORD": "mock-password",
        }.items()
    }
    mock = FastAPI(title="Mock Jira")

    @mock.exception_handler(MockJiraError)
    async def mock_error(_request: Request, error: MockJiraError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status,
            content={"errorMessages": [error.message]},
            headers={"WWW-Authenticate": "Basic"} if error.status == 401 else None,
        )

    def authorize(request: Request, ds: str, version: str) -> list[dict[str, Any]]:
        if ds not in datasets:
            raise MockJiraError("Unknown dataset", 404)
        pairs = (
            [(credentials["EMAIL"], credentials["API_TOKEN"])]
            if version == "3"
            else [(credentials["USERNAME"], credentials["PASSWORD"])]
        )
        accepted = [
            "Basic " + base64.b64encode(f"{name}:{password}".encode()).decode()
            for name, password in pairs
            if name and password
        ]
        if version == "2" and credentials["PAT"]:
            accepted.append("Bearer " + credentials["PAT"])
        supplied = request.headers.get("Authorization", "")
        if not any(
            secrets.compare_digest(supplied.encode(), value.encode())
            for value in accepted
        ):
            raise MockJiraError("Invalid or missing credentials", 401)
        return datasets[ds]

    @mock.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @mock.get("/{ds}/rest/api/{version}/myself")
    def myself(request: Request, ds: str, version: Literal["2", "3"]) -> dict[str, Any]:
        authorize(request, ds, version)
        return user_object(
            {
                "accountId": "mock-admin",
                "displayName": "Jira Admin",
                "emailAddress": credentials["EMAIL"],
            },
            version,
        ) | ({"name": credentials["USERNAME"]} if version == "2" else {})

    @mock.api_route("/{ds}/rest/api/3/search", methods=["GET", "POST"])
    def removed_search(request: Request, ds: str) -> None:
        authorize(request, ds, "3")
        raise MockJiraError("Use /rest/api/3/search/jql", 410)

    def project_list(request: Request, ds: str, version: str) -> list[dict[str, str]]:
        issues = authorize(request, ds, version)
        projects = {
            issue["key"].rsplit("-", 1)[0]: (issue["fields"].get("project") or {}).get(
                "name", issue["key"].rsplit("-", 1)[0]
            )
            for issue in issues
        }
        return [{"key": key, "name": name} for key, name in sorted(projects.items())]

    @mock.get("/{ds}/rest/api/3/project/search")
    def cloud_projects(request: Request, ds: str) -> dict[str, Any]:
        projects = project_list(request, ds, "3")
        return {"values": projects[:100], "isLast": len(projects) <= 100}

    @mock.get("/{ds}/rest/api/2/project")
    def dc_projects(request: Request, ds: str) -> list[dict[str, str]]:
        return project_list(request, ds, "2")

    async def search(request: Request, ds: str, version: str) -> dict[str, Any]:
        issues = authorize(request, ds, version)
        try:
            if request.method == "POST":
                params = Search.model_validate(await request.json())
            else:
                values: dict[str, Any] = dict(request.query_params)
                if "fields" in values:
                    values["fields"] = request.query_params.getlist("fields")
                params = Search.model_validate(values)
        except (ValidationError, ValueError) as error:
            raise MockJiraError("Invalid search parameters") from error
        issues = filter_issues(issues, params.jql)
        start = params.startAt if version == "2" else 0
        if version == "3" and params.nextPageToken is not None:
            if not re.fullmatch(r"[0-9]{1,12}", params.nextPageToken):
                raise MockJiraError("Invalid nextPageToken")
            start = int(params.nextPageToken)
        size = min(params.maxResults, 100)
        selected = params.fields
        names = (
            None
            if selected is None
            else {
                part.strip()
                for item in ([selected] if isinstance(selected, str) else selected)
                for part in item.split(",")
            }
        )
        page = []
        for original in issues[start : start + size]:
            issue = deepcopy(original)
            fields = issue["fields"]
            if version == "3":
                issue["expand"] = (
                    "names,schema,operations,editmeta,changelog,versionedRepresentations"
                )
                fields["issuetype"].update(
                    self="https://mock.example.com/rest/api/3/issuetype/10001",
                    id="10001",
                    description="Synthetic issue type",
                    iconUrl="https://mock.example.com/type",
                    subtask=False,
                    avatarId=10001,
                    entityId="synthetic-type",
                    hierarchyLevel=0,
                )
                category = {
                    "Done": (3, "done", "green"),
                    "In Progress": (4, "indeterminate", "yellow"),
                }.get(fields["status"]["name"], (2, "new", "blue-gray"))
                fields["status"].update(
                    self="https://mock.example.com/rest/api/3/status/10001",
                    id="10001",
                    description="Synthetic status",
                    iconUrl="https://mock.example.com/status",
                    statusCategory={
                        "self": f"https://mock.example.com/rest/api/3/statuscategory/{category[0]}",
                        "id": category[0],
                        "key": category[1],
                        "colorName": category[2],
                        "name": category[1],
                    },
                )
            for role in ("assignee", "reporter"):
                if fields.get(role):
                    fields[role] = user_object(fields[role], version)
            if version == "3" and fields.get("description") is not None:
                fields["description"] = {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": paragraph}],
                        }
                        for paragraph in re.split(r"\n\s*\n", fields["description"])
                        if paragraph
                    ],
                }
            if names is not None:
                issue["fields"] = {
                    key: value for key, value in fields.items() if key in names
                }
            issue["self"] = (
                f"{request.base_url}{ds}/rest/api/{version}/issue/{issue['id']}"
            )
            page.append(issue)
        if version == "2":
            return {
                "startAt": start,
                "maxResults": size,
                "total": len(issues),
                "issues": page,
            }
        result = {"issues": page, "isLast": start + size >= len(issues)}
        if not result["isLast"]:
            result["nextPageToken"] = str(start + size)
        return result

    @mock.api_route("/{ds}/rest/api/3/search/jql", methods=["GET", "POST"])
    async def cloud_search(request: Request, ds: str) -> dict[str, Any]:
        return await search(request, ds, "3")

    @mock.api_route("/{ds}/rest/api/2/search", methods=["GET", "POST"])
    async def dc_search(request: Request, ds: str) -> dict[str, Any]:
        return await search(request, ds, "2")

    return mock


app = create_mock_app(Path(__file__).resolve().parents[4] / "examples")
