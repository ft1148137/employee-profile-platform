from datetime import datetime
from ipaddress import ip_address
from re import fullmatch
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

ReasoningEffort = Literal["off", "minimal", "low", "medium", "high", "xhigh"]

ModelProvider = Literal[
    "openai",
    "anthropic",
    "deepseek",
    "qwen",
    "moonshotai",
    "moonshotai-cn",
    "kimi-coding",
    "openrouter",
    "openai-compatible",
    "ollama",
]
CLOUD_PROVIDERS = {
    "openai": ("openai-responses", "https://api.openai.com/v1"),
    "anthropic": ("anthropic-messages", "https://api.anthropic.com"),
    "deepseek": ("openai-completions", "https://api.deepseek.com"),
    "qwen": ("openai-completions", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    "moonshotai": ("openai-completions", "https://api.moonshot.ai/v1"),
    "moonshotai-cn": ("openai-completions", "https://api.moonshot.cn/v1"),
    "kimi-coding": ("anthropic-messages", "https://api.kimi.com/coding"),
    "openrouter": ("openai-completions", "https://openrouter.ai/api/v1"),
}
QWEN_URLS = (
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
)

ModelKind = Literal["cloud_api", "local", "openai_compatible", "chatgpt_subscription"]


class ModelSettingUpdate(BaseModel):
    enabled: bool
    kind: ModelKind | None = None
    provider: ModelProvider | None = None
    model: str | None = Field(default=None, max_length=200)
    base_url: str | None = Field(default=None, max_length=500)
    api_key: SecretStr | None = None
    reasoning_effort: ReasoningEffort | None = None

    @model_validator(mode="after")
    def validate_setting(self) -> Self:
        if self.kind == "chatgpt_subscription":
            self.model = self.model or "chatgpt/gpt-6.1-sol"
            self.reasoning_effort = self.reasoning_effort or "high"
            if (
                not self.model.startswith("chatgpt/")
                or self.base_url
                or self.api_key
                or self.provider
            ):
                raise ValueError("ChatGPT 订阅必须使用 chatgpt/ 模型，不配置地址或密钥")
            if not fullmatch(
                r"[A-Za-z0-9._-]{1,100}", self.model.removeprefix("chatgpt/")
            ):
                raise ValueError(
                    "ChatGPT 模型 ID 只能包含字母、数字、点、下划线和连字符，长度为 1–100"
                )
        if self.model:
            if (
                self.model.startswith("chatgpt/")
                and self.kind != "chatgpt_subscription"
            ):
                raise ValueError("chatgpt/ 只能用于 ChatGPT 订阅")
            if self.model.startswith("github_copilot/"):
                raise ValueError("不支持 GitHub Copilot 订阅登录")
        prefix = {"openai-compatible": "openai", "ollama": "ollama_chat"}.get(
            self.provider or "", self.provider
        )
        if prefix and self.model and len(f"{prefix}/{self.model}") > 200:
            raise ValueError("模型 ID 过长，包含提供方前缀后不能超过 200 个字符")
        if not self.enabled:
            return self
        if not self.kind or not self.model or not self.model.strip():
            raise ValueError("请选择模型类型并填写模型 ID")
        if self.kind != "chatgpt_subscription":
            if not self.provider:
                raise ValueError("请重新选择提供方")
            if self.kind == "cloud_api":
                if self.provider not in CLOUD_PROVIDERS:
                    raise ValueError("请选择云端模型提供方")
                if self.provider == "qwen":
                    if self.base_url not in QWEN_URLS:
                        raise ValueError("请选择百炼服务区域")
                elif self.base_url:
                    raise ValueError("云端提供方使用固定地址，不能填写服务地址")
            elif (
                self.provider
                != {"local": "ollama", "openai_compatible": "openai-compatible"}[
                    self.kind
                ]
            ):
                raise ValueError("模型类型与提供方不匹配")
        if self.kind in ("local", "openai_compatible") and not self.base_url:
            raise ValueError("请填写模型服务地址")
        if self.base_url:
            try:
                url = TypeAdapter(HttpUrl).validate_python(self.base_url)
            except ValidationError:
                raise ValueError("请填写有效的 http 或 https 模型服务地址") from None
            try:
                address = ip_address((url.host or "").strip("[]"))
            except ValueError:
                address = None
            if address:
                address = getattr(address, "ipv4_mapped", None) or address
                if address.is_link_local or str(address) == "fd00:ec2::254":
                    raise ValueError("模型服务地址不能使用链路本地或云元数据地址")
            if url.username is not None or url.password is not None:
                raise ValueError("模型服务地址不能带用户名密码")
        return self


class ChatGPTAccount(BaseModel):
    account_id: str | None = None
    email: str | None = None
    plan_type: str | None = None
    needs_relogin: bool = False


class ChatGPTStatus(ChatGPTAccount):
    connected: bool = False


class ChatGPTLogin(BaseModel):
    status: Literal["none", "pending", "connected", "expired", "error"]
    verification_url: str | None = None
    expires_at: datetime | None = None
    account: ChatGPTAccount | None = None
    message: str | None = None


class ChatGPTCallback(BaseModel):
    input: SecretStr = Field(max_length=4096)


class ModelSettingPublic(BaseModel):
    enabled: bool = False
    kind: str | None = None
    provider: ModelProvider | None = None
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
