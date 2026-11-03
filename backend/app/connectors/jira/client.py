from datetime import UTC, datetime
from typing import Any, Literal, Self

import httpx
from pydantic import BaseModel

from app.connectors.jira.text import adf_to_text, wiki_to_text
from app.core.secrets import decrypt_secret
from app.domains.source.models import SourceConnection
from app.domains.source.schemas import JiraConfig

NETWORK_MESSAGE = (
    "平台服务器连不上这个地址。接 Jira Cloud 需要服务器能访问外网；"
    "接 Data Center 需要和 Jira 在同一内网。也可以改用 CSV 导入工作记录。"
)


class SourceAuthError(Exception):
    pass


class SourceUnreachable(Exception):
    pass


class RetryableSourceError(Exception):
    def __init__(self, message: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class JiraPerson(BaseModel):
    role: Literal["assignee", "reporter"]
    id: str
    display_name: str | None = None
    email: str | None = None


class JiraIssue(BaseModel):
    key: str
    url: str
    summary: str
    issue_type: str | None = None
    status: str | None = None
    created: datetime
    updated: datetime
    resolved: datetime | None = None
    labels: list[str] = []
    components: list[str] = []
    description: str = ""
    people: list[JiraPerson] = []


class JiraPage(BaseModel):
    issues: list[JiraIssue]
    next_cursor: str | None = None


class JiraClient:
    version: str

    def __init__(
        self, config: JiraConfig, secret: str, http: httpx.Client | None
    ) -> None:
        self.config = config
        self._owns_http = http is None
        self._http = http if http is not None else httpx.Client()
        self._auth = (
            httpx.BasicAuth(config.username or "", secret)
            if config.auth_type != "pat"
            else None
        )
        self._headers = (
            {"Authorization": f"Bearer {secret}"} if self._auth is None else {}
        )

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self._http.request(
                method,
                f"{self.config.base_url}/rest/api/{self.version}/{path}",
                auth=self._auth,
                headers=self._headers,
                follow_redirects=False,
                timeout=httpx.Timeout(30, connect=10),
                **kwargs,
            )
        except httpx.ReadTimeout:
            raise RetryableSourceError(NETWORK_MESSAGE) from None
        except httpx.RequestError as error:
            message = (
                "站点的 HTTPS 证书无法验证"
                if "certificate verify failed" in str(error).lower().replace("_", " ")
                else NETWORK_MESSAGE
            )
            raise SourceUnreachable(message) from None
        if response.status_code in (401, 403):
            raise SourceAuthError(
                "账号或令牌不正确"
                if response.status_code == 401
                else "账号没有浏览或搜索 issue 的权限"
            )
        if response.status_code in (404, 410):
            raise SourceUnreachable("地址不对，或者 Jira 类型选错了")
        if response.status_code == 429 or response.status_code >= 500:
            retry = response.headers.get("Retry-After", "")
            raise RetryableSourceError(
                "Jira 暂时不可用，请稍后重试", int(retry) if retry.isdigit() else None
            )
        if not response.is_success:
            raise SourceUnreachable("Jira 请求失败，请检查地址和配置")
        try:
            data = response.json()
            if isinstance(data, dict):
                return data
        except ValueError:
            pass
        raise SourceUnreachable("Jira 返回的数据格式不正确")

    def current_account(self) -> str:
        data = self._request("GET", "myself")
        return str(data.get("displayName") or data.get("name") or "")

    def search(
        self, since: datetime, cursor: str | None, page_size: int = 100
    ) -> JiraPage:
        fields = [
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
        ]
        if self.config.include_description:
            fields.append("description")
        jql = f'updated >= "{since.astimezone(UTC):%Y-%m-%d %H:%M}" ORDER BY updated ASC, key ASC'
        data, next_cursor = self._search(jql, fields, cursor, page_size)
        try:
            return JiraPage(
                issues=[self._normalize(issue) for issue in data["issues"]],
                next_cursor=next_cursor,
            )
        except ValueError, KeyError, TypeError:
            raise SourceUnreachable("Jira 返回的数据格式不正确") from None

    def _search(
        self, jql: str, fields: list[str], cursor: str | None, page_size: int
    ) -> tuple[dict[str, Any], str | None]:
        raise NotImplementedError

    def _normalize(self, issue: dict[str, Any]) -> JiraIssue:
        fields = issue["fields"]
        people = []
        for role in ("assignee", "reporter"):
            user = fields.get(role)
            if not user:
                continue
            identifier = (
                user.get("accountId")
                if self.version == "3"
                else user.get("key") or user.get("name")
            )
            if identifier:
                people.append(
                    JiraPerson(
                        role=role,
                        id=identifier,
                        display_name=user.get("displayName"),
                        email=user.get("emailAddress"),
                    )
                )
        description = ""
        if self.config.include_description:
            description = (
                adf_to_text(fields.get("description"))
                if self.version == "3"
                else wiki_to_text(fields.get("description"))
            ).removesuffix("\n")[:4000]
        return JiraIssue(
            key=issue["key"],
            url=f"{self.config.base_url}/browse/{issue['key']}",
            summary=fields["summary"],
            issue_type=(fields.get("issuetype") or {}).get("name"),
            status=(fields.get("status") or {}).get("name"),
            created=fields["created"],
            updated=fields["updated"],
            resolved=fields.get("resolutiondate"),
            labels=fields.get("labels") or [],
            components=[c["name"] for c in fields.get("components") or []],
            description=description,
            people=people,
        )


class CloudJiraClient(JiraClient):
    version = "3"

    def _search(
        self, jql: str, fields: list[str], cursor: str | None, page_size: int
    ) -> tuple[dict[str, Any], str | None]:
        body: dict[str, Any] = {"jql": jql, "fields": fields, "maxResults": page_size}
        if cursor is not None:
            body["nextPageToken"] = cursor
        data = self._request("POST", "search/jql", json=body)
        return data, None if data.get("isLast") else data.get("nextPageToken")


class DataCenterJiraClient(JiraClient):
    version = "2"

    def _search(
        self, jql: str, fields: list[str], cursor: str | None, page_size: int
    ) -> tuple[dict[str, Any], str | None]:
        start = int(cursor or 0)
        data = self._request(
            "GET",
            "search",
            params={
                "jql": jql,
                "fields": ",".join(fields),
                "maxResults": page_size,
                "startAt": start,
            },
        )
        try:
            end = start + len(data["issues"])
            return data, str(end) if end < data["total"] and end > start else None
        except KeyError, TypeError:
            raise SourceUnreachable("Jira 返回的数据格式不正确") from None


def jira_client_for(
    connection: SourceConnection, http: httpx.Client | None = None
) -> JiraClient:
    if not connection.secret_ciphertext:
        raise SourceUnreachable("请先保存 Jira 凭据")
    config = JiraConfig.model_validate(connection.config)
    client = CloudJiraClient if config.deployment == "cloud" else DataCenterJiraClient
    return client(config, decrypt_secret(connection.secret_ciphertext), http)
