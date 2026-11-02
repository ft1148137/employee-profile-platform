from collections.abc import Generator
from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlmodel import Session

from app.api.deps import get_db
from app.main import app


def test_health_check(client: TestClient) -> None:
    response = client.get("/api/v1/utils/health-check/")
    assert response.status_code == 200
    assert response.json() is True


def test_health_check_database_unavailable(client: TestClient) -> None:
    def unavailable_db() -> Generator[Session]:
        session = MagicMock(spec=Session)
        session.exec.side_effect = OperationalError(
            "SELECT 1", {}, Exception("private connection details")
        )
        yield session

    app.dependency_overrides[get_db] = unavailable_db
    try:
        response = client.get("/api/v1/utils/health-check/")
        assert response.status_code == 503
        assert response.json() == {"detail": "数据库暂时不可用"}
    finally:
        app.dependency_overrides.pop(get_db)
