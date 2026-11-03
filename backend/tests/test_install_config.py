from pathlib import Path

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


@pytest.mark.parametrize("demo", [False, True])
def test_install_mock_overlay(tmp_path: Path, demo: bool) -> None:
    import os
    import shutil
    import subprocess

    root = Path(__file__).resolve().parents[2]
    shutil.copy(root / "install.sh", tmp_path)
    (tmp_path / "deploy").mkdir()
    shutil.copy(root / "deploy/common.sh", tmp_path / "deploy")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    docker = binaries / "docker"
    docker.write_text("""#!/bin/sh
case "$*" in
  'compose version --short') echo 2.40.0 ;;
  'compose config --environment') cat .env ;;
esac
""")
    docker.chmod(0o755)
    curl = binaries / "curl"
    curl.write_text("#!/bin/sh\nexit 0\n")
    curl.chmod(0o755)
    subprocess.run(
        ["bash", "install.sh", "--non-interactive", *(["--demo"] if demo else [])],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": f"{binaries}:{os.environ['PATH']}",
            "EPP_ADDRESS": "localhost",
            "EPP_HTTP_PORT": "18953",
            "EPP_ACCESS": "http",
            "COMPOSE_PROJECT_NAME": "nere53-install",
        },
        check=True,
        capture_output=True,
        text=True,
    )
    config = (tmp_path / ".env").read_text()
    files = next(
        line for line in config.splitlines() if line.startswith("COMPOSE_FILE=")
    )
    assert ("deploy/compose.mock-jira.yml" in files) is demo
