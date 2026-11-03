import asyncio
import os

from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.core.config import settings
from app.core.db import engine
from app.worker.app import app as worker_app


def test_database_isolation(migration_template: str) -> None:
    url = make_url(os.environ["DATABASE_URL"])
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    assert f"_{worker}_" in url.database
    assert engine.url == make_url(str(settings.DATABASE_URL)) == url
    assert migration_template.startswith("migration_template_")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT current_database()")) == url.database

    async def queue_database() -> str:
        async with worker_app.open_async():
            row = await worker_app.connector.execute_query_one_async(
                "SELECT current_database() AS name"
            )
            return row["name"]

    assert asyncio.run(queue_database()) == url.database
