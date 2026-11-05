from datetime import datetime
from typing import Literal


class ModelUnavailable(Exception):
    pass


class ModelServiceError(ModelUnavailable):
    def __init__(
        self,
        code: Literal["model_not_found", "upstream_rejected", "upstream_unavailable"],
    ) -> None:
        super().__init__(
            {
                "model_not_found": "这个模型不可用，请换一个模型",
                "upstream_rejected": "模型服务拒绝了请求，请检查模型设置",
                "upstream_unavailable": "模型服务暂时不可用，请稍后重试",
            }[code]
        )


class ModelAuthenticationError(ModelUnavailable):
    def __init__(self) -> None:
        super().__init__("密钥不对或没有权限")


class ModelOutputInvalid(Exception):
    def __init__(self) -> None:
        super().__init__("模型输出不符合格式")


class ModelRateLimited(Exception):
    def __init__(
        self, retry_at: datetime | None, reason: str = "模型用量到上限"
    ) -> None:
        self.retry_at = retry_at
        self.reason = reason
        super().__init__(reason)
