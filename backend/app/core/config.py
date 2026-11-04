import warnings
from pathlib import Path
from typing import Literal, Self

from pydantic import (
    EmailStr,
    Field,
    HttpUrl,
    PostgresDsn,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Use top level .env file (one level above ./backend/)
        env_file="../.env",
        env_ignore_empty=True,
        extra="ignore",
    )
    API_V1_STR: str = "/api/v1"
    SECRET_KEY: str
    # 60 minutes * 24 hours * 8 days = 8 days
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 8
    LOGIN_MAX_FAILURES: int = Field(default=5, gt=0)
    LOGIN_MAX_FAILURES_PER_IP: int = Field(default=300, gt=0)
    LOGIN_LOCK_MINUTES: int = Field(default=15, gt=0, le=1440)
    FRONTEND_HOST: str = "http://localhost:5173"
    FASTAPI_ENV: Literal["development"] | None = None
    CHATGPT_CODEX_AUTH_FILE: Path | None = None
    PI_URL: str = "http://pi:8081"
    PI_SERVICE_TOKEN: SecretStr = Field(default=SecretStr("changethis"), repr=False)
    SEED_DEMO_DATA: bool = False
    DEMO_USER_PASSWORD: str | None = Field(default=None, min_length=8, max_length=128)
    DEMO_DATA_DIR: Path = Path(__file__).resolve().parents[3] / "examples/demo-org"
    CAPABILITY_TEMPLATES_DIR: Path = (
        Path(__file__).resolve().parents[3] / "templates/capabilities"
    )

    PROJECT_NAME: str
    SENTRY_DSN: HttpUrl | None = None
    DATABASE_URL: PostgresDsn

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def _use_psycopg_driver(cls, value: str | PostgresDsn) -> str:
        database_url = str(value)
        for scheme in ("postgres://", "postgresql://"):
            if database_url.startswith(scheme):
                return database_url.replace(scheme, "postgresql+psycopg://", 1)
        return database_url

    EMAIL_TEST_USER: EmailStr = "test@example.com"
    FIRST_SUPERUSER: EmailStr | None = None
    FIRST_SUPERUSER_PASSWORD: str | None = None

    def _check_default_secret(self, var_name: str, value: str | None) -> None:
        if value == "changethis":
            message = (
                f'The value of {var_name} is "changethis", '
                "for security, please change it, at least for deployments."
            )
            if self.FASTAPI_ENV == "development":
                warnings.warn(message, stacklevel=1)
            else:
                raise ValueError(message)

    @model_validator(mode="after")
    def _enforce_non_default_secrets(self) -> Self:
        if bool(self.FIRST_SUPERUSER) != bool(self.FIRST_SUPERUSER_PASSWORD):
            raise ValueError(
                "Configure both FIRST_SUPERUSER and FIRST_SUPERUSER_PASSWORD, or neither"
            )
        self._check_default_secret("SECRET_KEY", self.SECRET_KEY)
        self._check_default_secret(
            "PI_SERVICE_TOKEN", self.PI_SERVICE_TOKEN.get_secret_value()
        )
        for host in self.DATABASE_URL.hosts():
            self._check_default_secret("DATABASE_URL password", host["password"])
        self._check_default_secret(
            "FIRST_SUPERUSER_PASSWORD", self.FIRST_SUPERUSER_PASSWORD
        )

        if self.CHATGPT_CODEX_AUTH_FILE and self.FASTAPI_ENV != "development":
            raise ValueError("CHATGPT_CODEX_AUTH_FILE 只允许在开发模式使用")
        return self


settings = Settings()  # type: ignore # ty: ignore[unused-ignore-comment]
