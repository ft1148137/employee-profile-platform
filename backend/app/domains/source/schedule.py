"""Calendar schedules and atomic dispatch for Jira connections."""

from calendar import monthrange
from datetime import datetime

from sqlmodel import Session, col, select

from app.domains.base import utc_now
from app.domains.source.models import SourceConnection, SyncRun
from app.domains.source.sync import _enqueue, active_sync
from app.domains.work.ingest import IngestCounts


def add_period(anchor: datetime, schedule: str) -> datetime:
    months = {"monthly": 1, "quarterly": 3, "yearly": 12}[schedule]
    year, month = divmod(anchor.year * 12 + anchor.month - 1 + months, 12)
    month += 1
    return anchor.replace(
        year=year, month=month, day=min(anchor.day, monthrange(year, month)[1])
    )


def next_sync_time(
    session: Session, connection: SourceConnection, schedule: str
) -> datetime | None:
    if schedule == "off":
        return None
    last_success = session.exec(
        select(SyncRun.finished_at)
        .where(
            SyncRun.org_id == connection.org_id,
            SyncRun.connection_id == connection.id,
            SyncRun.status == "succeeded",
            col(SyncRun.finished_at).is_not(None),
        )
        .order_by(col(SyncRun.finished_at).desc())
        .limit(1)
    ).first()
    return add_period(last_success or utc_now(), schedule)


def dispatch_due_syncs(session: Session, now: datetime) -> None:
    # The system dispatcher discovers tenants; each run keeps its connection's org_id.
    connections = session.exec(
        select(SourceConnection)
        .where(
            SourceConnection.kind == "jira",
            SourceConnection.sync_schedule != "off",
            col(SourceConnection.next_sync_at) <= now,
        )
        .with_for_update(skip_locked=True)
    ).all()
    for connection in connections:
        assert connection.next_sync_at is not None
        due = connection.next_sync_at
        while due <= now:
            due = add_period(due, connection.sync_schedule)
        connection.next_sync_at = due
        session.add(connection)
        if not active_sync(session, connection):
            run = SyncRun(
                org_id=connection.org_id,
                connection_id=connection.id,
                requested_by_user_id=connection.updated_by_user_id,
                trigger="schedule",
                counts={"records": 0, **IngestCounts().model_dump()},
            )
            session.add(run)
            _enqueue(session, run)
    session.commit()
