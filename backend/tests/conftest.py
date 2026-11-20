import os
from contextlib import ExitStack
from pathlib import Path
from uuid import uuid4

import pytest
from dotenv import dotenv_values
from sqlalchemy.engine import URL, make_url
from xdist.workermanage import WorkerController

DATABASES = pytest.StashKey[ExitStack]()
SHARED_TEMPLATE = pytest.StashKey[str]()


def _database_url() -> URL:
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    return make_url(os.environ.get("DATABASE_URL") or env["DATABASE_URL"]).set(
        drivername="postgresql+psycopg"
    )


def _create_template(stack: ExitStack, url: URL) -> str:
    from tests.utils.migrations import isolated_database, migrate

    template = f"migration_template_{uuid4().hex}"
    engine = stack.enter_context(isolated_database(url, template))
    migrate(engine, "upgrade", "head")
    engine.dispose()
    return template


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node: WorkerController) -> None:
    config = node.config
    template = config.stash.get(SHARED_TEMPLATE, None)
    if template is None:
        with ExitStack() as stack:
            template = _create_template(stack, _database_url())
            config.stash[SHARED_TEMPLATE] = template
            config.add_cleanup(stack.pop_all().close)
    node.workerinput["migration_template"] = template


@pytest.hookimpl(trylast=True)
def pytest_sessionstart(session: pytest.Session) -> None:
    # pytest-cov starts worker coverage in sessionstart, before application imports.
    config = session.config
    if config.getoption("numprocesses", default=0) and not hasattr(
        config, "workerinput"
    ):
        return

    from tests.utils.migrations import MIGRATION_TEMPLATE, isolated_database

    with ExitStack() as stack:
        url = _database_url()
        worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
        database = f"{(url.database or 'app')[:20]}_{worker}_{uuid4().hex}"
        patch = stack.enter_context(pytest.MonkeyPatch.context())
        # Configure before importing any app module, including Alembic's model imports.
        patch.setenv(
            "DATABASE_URL",
            url.set(database=database).render_as_string(hide_password=False),
        )
        template = (
            config.workerinput["migration_template"]
            if hasattr(config, "workerinput")
            else _create_template(stack, url)
        )
        stack.enter_context(isolated_database(url, database, template=template))
        config.stash[MIGRATION_TEMPLATE] = template
        config.pluginmanager.import_plugin("tests.fixtures")
        from app.core.db import engine

        stack.callback(engine.dispose)
        config.stash[DATABASES] = stack.pop_all()
        config.add_cleanup(config.stash[DATABASES].close)


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session) -> None:
    # xdist may terminate workers after workerfinished, before config cleanup runs.
    if databases := session.config.stash.get(DATABASES, None):
        databases.close()
