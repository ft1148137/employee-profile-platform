import os
from contextlib import ExitStack
from pathlib import Path
from uuid import uuid4

import pytest
from dotenv import dotenv_values
from sqlalchemy.engine import make_url

DATABASES = pytest.StashKey[ExitStack]()


@pytest.hookimpl(trylast=True)
def pytest_sessionstart(session: pytest.Session) -> None:
    # pytest-cov starts worker coverage in sessionstart, before application imports.
    config = session.config
    if config.getoption("numprocesses", default=0) and not hasattr(
        config, "workerinput"
    ):
        return

    from tests.utils.migrations import MIGRATION_TEMPLATE, isolated_database, migrate

    with ExitStack() as stack:
        env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
        url = make_url(os.environ.get("DATABASE_URL") or env["DATABASE_URL"])
        url = url.set(drivername="postgresql+psycopg")
        worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
        database = f"{(url.database or 'app')[:20]}_{worker}_{uuid4().hex}"
        patch = stack.enter_context(pytest.MonkeyPatch.context())
        # Configure before importing any app module, including Alembic's model imports.
        patch.setenv(
            "DATABASE_URL",
            url.set(database=database).render_as_string(hide_password=False),
        )
        template = f"migration_template_{uuid4().hex}"
        template_engine = stack.enter_context(isolated_database(url, template))
        migrate(template_engine, "upgrade", "head")
        template_engine.dispose()
        stack.enter_context(isolated_database(url, database, template=template))
        config.stash[MIGRATION_TEMPLATE] = template
        config.pluginmanager.import_plugin("tests.fixtures")
        config.stash[DATABASES] = stack.pop_all()
        config.add_cleanup(config.stash[DATABASES].close)


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session) -> None:
    # xdist may terminate workers after workerfinished, before config cleanup runs.
    if databases := session.config.stash.get(DATABASES, None):
        databases.close()
