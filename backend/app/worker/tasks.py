from app.worker.app import app


@app.task(name="system.ping")
async def ping() -> None:
    """Verify queue execution without accessing tenant data or external services."""
