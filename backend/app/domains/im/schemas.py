from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

Platform = Literal["feishu", "wecom", "telegram"]


class ImSettingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    email_auto_bind: bool | None = None
    credentials: dict[str, Annotated[SecretStr, Field(max_length=4096)]] = Field(
        default_factory=dict, max_length=4
    )
    data_transfer_acknowledged: bool = False


class ImSettingPublic(BaseModel):
    platform: Platform
    enabled: bool = False
    email_auto_bind: bool = True
    has_credentials: bool = False
    updated_at: datetime | None = None


class ImConnectionTest(BaseModel):
    ok: bool
    message: str
