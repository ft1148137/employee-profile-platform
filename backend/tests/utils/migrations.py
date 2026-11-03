from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL

MIGRATION_TEMPLATE = pytest.StashKey[str]()


@contextmanager
def isolated_database(
    url: URL, database: str, *, template: str | None = None
) -> Generator[Engine]:
    admin_url = url.set(drivername="postgresql", database="postgres")
    with psycopg.connect(
        admin_url.render_as_string(hide_password=False), autocommit=True
    ) as admin:
        statement = sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database))
        if template is not None:
            statement += sql.SQL(" TEMPLATE {}").format(sql.Identifier(template))
        admin.execute(statement)
        isolated = create_engine(url.set(database=database))
        try:
            yield isolated
        finally:
            isolated.dispose()
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(database)
                )
            )


def migrate(
    engine: Engine, action: Literal["upgrade", "downgrade"], revision: str
) -> None:
    backend = Path(__file__).resolve().parents[2]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "app/alembic"))
    config.attributes["configure_logger"] = False
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        if action == "upgrade":
            command.upgrade(config, revision)
        else:
            command.downgrade(config, revision)
