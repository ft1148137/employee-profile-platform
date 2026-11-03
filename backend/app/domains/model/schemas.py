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

ReasoningEffort = Literal["low", "medium", "high", "xhigh"]

ModelKind = Literal["cloud_api", "local", "openai_compatible", "chatgpt_subscription"]


class ModelSettingUpdate(BaseModel):
    enabled: bool
    kind: ModelKind | None = None
    model: str | None = Field(default=None, max_length=200)
    base_url: str | None = Field(default=None, max_length=500)
    api_key: SecretStr | None = None
    reasoning_effort: ReasoningEffort | None = None

    @model_validator(mode="after")
    def validate_setting(self) -> Self:
        if self.kind == "chatgpt_subscription":
            self.model = self.model or "chatgpt/gpt-6.1-sol"
            self.reasoning_effort = self.reasoning_effort or "high"
            if not self.model.startswith("chatgpt/") or self.base_url or self.api_key:
                raise ValueError("ChatGPT 订阅必须使用 chatgpt/ 模型，不配置地址或密钥")
        if self.model:
            if (
                self.model.startswith("chatgpt/")
                and self.kind != "chatgpt_subscription"
            ):
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


class ChatGPTAccount(BaseModel):
    email: str | None = None
    plan_type: str | None = None
    needs_relogin: bool = False


class ChatGPTStatus(ChatGPTAccount):
    connected: bool = False


class ChatGPTLogin(BaseModel):
    status: Literal["none", "pending", "connected", "expired", "error"]
    verification_url: str | None = None
    user_code: str | None = None
    expires_at: datetime | None = None
    account: ChatGPTAccount | None = None
    message: str | None = None


class ModelSettingPublic(BaseModel):
    local_codex: bool = False
    enabled: bool = False
    kind: str | None = None
    model: str | None = None
    base_url: str | None = None
    has_api_key: bool = False
    reasoning_effort: ReasoningEffort | None = None
    chatgpt: ChatGPTStatus = Field(default_factory=ChatGPTStatus)
    paused_until: datetime | None = None
    pause_reason: str | None = None
    updated_at: datetime | None = None


class ConnectionTest(BaseModel):
    ok: bool
    message: str
