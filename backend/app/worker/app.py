from procrastinate import App, PsycopgConnector

from app.core.config import settings

app = App(
    connector=PsycopgConnector(
        conninfo=str(settings.DATABASE_URL).replace(
            "postgresql+psycopg://", "postgresql://", 1
        ),
    ),
    import_paths=["app.worker.tasks"],
)
