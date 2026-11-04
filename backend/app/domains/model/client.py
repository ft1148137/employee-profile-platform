from datetime import datetime


class ModelUnavailable(Exception):
    pass


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
