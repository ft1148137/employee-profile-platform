from calendar import monthrange
from datetime import date, datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl, SecretStr, TypeAdapter, model_validator

from app.domains.org.models import EmploymentStatus
from app.domains.org.schemas import DepartmentSummary
from app.domains.work.ingest import IngestCounts


class IdentityEmployee(BaseModel):
    id: UUID
    employee_code: str | None
    display_name: str
    department: DepartmentSummary | None
    employment_status: EmploymentStatus


class IdentityCandidate(IdentityEmployee):
    reason: Literal["email", "name"]


class ExternalIdentityPublic(BaseModel):
    id: UUID
    source: str
    source_label: str
    external_id: str
    display_name: str | None
    email: str | None
    record_count: int
    employee: IdentityEmployee | None
    candidate: IdentityCandidate | None


class ExternalIdentitiesPublic(BaseModel):
    data: list[ExternalIdentityPublic]
    count: int


class IdentityLink(BaseModel):
    identity_id: UUID
    employee_id: UUID


class IdentityLinks(BaseModel):
    links: list[IdentityLink] = Field(min_length=1, max_length=200)


class IdentityLinkResult(BaseModel):
    linked: int
    counts: IngestCounts


class IdentityUnlinkResult(BaseModel):
    evidence_retracted: int


def default_start_date() -> date:
    today = date.today()
    year = today.year - 1
    return today.replace(
        year=year, day=min(today.day, monthrange(year, today.month)[1])
    )


class JiraConfig(BaseModel):
    deployment: Literal["cloud", "data_center"]
    base_url: str = Field(max_length=500)
    auth_type: Literal["api_token", "pat", "basic"]
    username: str | None = Field(default=None, max_length=255)
    start_date: date = Field(default_factory=default_start_date)
    include_description: bool = False

    @model_validator(mode="after")
    def validate_config(self) -> Self:
        url = TypeAdapter(HttpUrl).validate_python(self.base_url)
        if (
            url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
        ):
            raise ValueError("Jira 地址不能带用户名、密码、查询参数或片段")
        self.base_url = str(url).rstrip("/")
        if self.deployment == "cloud" and self.auth_type != "api_token":
            raise ValueError("Jira Cloud 请使用邮箱和 API token")
        if self.deployment == "data_center" and self.auth_type not in ("pat", "basic"):
            raise ValueError("Data Center 请使用个人访问令牌或用户名和密码")
        if self.auth_type != "pat" and not self.username:
            raise ValueError("请填写 Jira 账号")
        if self.auth_type == "pat":
            self.username = None
        return self


class JiraConnectionUpdate(JiraConfig):
    secret: SecretStr | None = Field(default=None, min_length=1)


class JiraConnectionPublic(BaseModel):
    configured: bool = False
    config: JiraConfig | None = None
    has_secret: bool = False
    synced_until: datetime | None = None
    updated_at: datetime | None = None


class JiraConnectionTest(BaseModel):
    ok: bool
    message: str
    account_name: str | None = None
