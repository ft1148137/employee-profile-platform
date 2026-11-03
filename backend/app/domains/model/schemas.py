from datetime import datetime
from typing import Literal, Self

from pydantic import (
    BaseModel,
    Field,
    HttpUrl,
    SecretStr,
    TypeAdapter,
    ValidationError,
    model_validator,
)

ModelKind = Literal["cloud_api", "local", "openai_compatible", "chatgpt_subscription"]


class ModelSettingUpdate(BaseModel):
    enabled: bool
    kind: ModelKind | None = None
    model: str | None = Field(default=None, max_length=200)
    base_url: str | None = Field(default=None, max_length=500)
    api_key: SecretStr | None = None

    @model_validator(mode="after")
    def validate_setting(self) -> Self:
        if self.kind == "chatgpt_subscription":
            raise ValueError("请先在设置页连接 ChatGPT")
        if self.model:
            if self.model.startswith("chatgpt/"):
                raise ValueError("chatgpt/ 只能用于 ChatGPT 订阅")
            if self.model.startswith("github_copilot/"):
                raise ValueError("不支持 GitHub Copilot 订阅登录")
        if not self.enabled:
            return self
        if not self.kind or not self.model or not all(self.model.partition("/")[::2]):
            raise ValueError("请选择模型类型并填写带提供方前缀的模型名")
        if self.kind == "openai_compatible" and not self.model.startswith("openai/"):
            raise ValueError("通用 OpenAI 兼容接口的模型名必须以 openai/ 开头")
        if self.kind in ("local", "openai_compatible") and not self.base_url:
            raise ValueError("请填写模型服务地址")
        if self.base_url:
            try:
                url = TypeAdapter(HttpUrl).validate_python(self.base_url)
            except ValidationError:
                raise ValueError("请填写有效的 http 或 https 模型服务地址") from None
            if url.username is not None or url.password is not None:
                raise ValueError("模型服务地址不能带用户名密码")
        return self


class ModelSettingPublic(BaseModel):
    enabled: bool = False
    kind: str | None = None
    model: str | None = None
    base_url: str | None = None
    has_api_key: bool = False
    updated_at: datetime | None = None


class ConnectionTest(BaseModel):
    ok: bool
    message: str
