from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Name = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
]


class TokenCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name


class TokenPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    prefix: str
    scopes: Literal["employee"]
    client_kind: Literal["pat", "oauth"]
    created_at: datetime
    expires_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class TokenCreated(TokenPublic):
    token: str = Field(repr=False)


class McpSettingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    allowed_clients: list[Name] = Field(default_factory=list, max_length=100)


class McpSettingPublic(McpSettingUpdate):
    updated_by: UUID | None = None
    updated_at: datetime | None = None
