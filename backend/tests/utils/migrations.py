from pathlib import Path
from typing import Literal

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine


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
