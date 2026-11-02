import pytest
from pydantic import ValidationError
from sqlmodel import Session

from app.core.config import Settings, settings
from app.core.db import init_db


def test_install_without_superuser(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIRST_SUPERUSER", "")
    monkeypatch.setenv("FIRST_SUPERUSER_PASSWORD", "")
    config = Settings(
        _env_file=None,  # type: ignore[call-arg]
        PROJECT_NAME="test",
        SECRET_KEY="random-test-secret",
        DATABASE_URL="postgresql://postgres:test-password@localhost/app",
    )
    assert config.FIRST_SUPERUSER is None
    assert config.FIRST_SUPERUSER_PASSWORD is None
    monkeypatch.setattr(settings, "FIRST_SUPERUSER", None)
    monkeypatch.setattr(settings, "FIRST_SUPERUSER_PASSWORD", None)
    # An unbound session fails if init_db attempts any database access.
    with Session() as session:
        init_db(session)


@pytest.mark.parametrize(
    "values",
    [
        {"FIRST_SUPERUSER": "admin@example.com"},
        {"FIRST_SUPERUSER_PASSWORD": "valid-password"},
        {"SECRET_KEY": "changethis"},
        {"DATABASE_URL": "postgresql://postgres:changethis@localhost/app"},
    ],
)
def test_install_rejects_invalid_config(
    monkeypatch: pytest.MonkeyPatch, values: dict[str, str]
) -> None:
    for key in ("FIRST_SUPERUSER", "FIRST_SUPERUSER_PASSWORD", "FASTAPI_ENV"):
        monkeypatch.delenv(key, raising=False)
    config = {
        "PROJECT_NAME": "test",
        "SECRET_KEY": "random-test-secret",
        "DATABASE_URL": "postgresql://postgres:test-password@localhost/app",
        **values,
    }
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **config)  # type: ignore[call-arg, arg-type]
