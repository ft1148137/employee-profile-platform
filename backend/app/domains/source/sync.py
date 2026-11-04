"""Transactional queueing and resumable, serialized source ingestion."""

from datetime import UTC, datetime, time, timedelta
from uuid import UUID

from procrastinate import RetryDecision
from procrastinate.exceptions import JobRetry
from pydantic import JsonValue
from sqlalchemy import text, tuple_
from sqlmodel import Session, col, select

from app.authz.principal import Principal
from app.connectors.jira.client import (
    JiraIssue,
    RetryableSourceError,
    SourceAuthError,
    SourceUnreachable,
    jira_client_for,
)
from app.core.db import engine
from app.core.secrets import SecretUnavailable
from app.domains.audit.models import ActorKind
from app.domains.audit.service import record_audit
from app.domains.base import utc_now
from app.domains.org.models import ExternalIdentity, Organization
from app.domains.source.models import SourceConnection, SyncRun
from app.domains.source.schemas import JiraConfig, SyncRunPublic
from app.domains.work.ingest import (
    IngestCounts,
    SourcePerson,
    SourceRecord,
    ingest_record,
)
from app.domains.work.records import WorkRecordRow
from app.worker.app import app


class SyncError(Exception):
    def __init__(self, status: int, message: str) -> None:
        self.status, self.message = status, message


def public_run(run: SyncRun, kind: str) -> SyncRunPublic:
    return SyncRunPublic.model_validate(
        {
            **run.model_dump(exclude={"input"}),
            "kind": kind,
            "filename": str(run.input["filename"]) if run.input else None,
        }
    )


def _lock_org(session: Session, org_id: UUID) -> None:
    session.exec(
        select(Organization).where(Organization.id == org_id).with_for_update()
    ).one()


def active_sync(session: Session, connection: SourceConnection) -> bool:
    return (
        session.exec(
            select(SyncRun).where(
                SyncRun.org_id == connection.org_id,
                SyncRun.connection_id == connection.id,
                col(SyncRun.status).in_(("queued", "running")),
            )
        ).first()
        is not None
    )


def _enqueue(session: Session, run: SyncRun) -> None:
    session.flush()
    app.configure_task(
        "sources.sync_run",
        connection=session.connection().connection.driver_connection,
    ).defer(org_id=str(run.org_id), run_id=str(run.id), initial_attempts=run.attempts)


def create_run(
    session: Session,
    principal: Principal,
    kind: str,
    input_data: dict[str, JsonValue] | None = None,
) -> SyncRunPublic:
    assert principal.user_id is not None
    _lock_org(session, principal.org_id)
    connection = session.exec(
        select(SourceConnection)
        .where(
            SourceConnection.org_id == principal.org_id,
            SourceConnection.kind == kind,
        )
        .with_for_update()
    ).one_or_none()
    if connection is None:
        if kind == "jira":
            raise SyncError(422, "请先配置 Jira")
        connection = SourceConnection(
            org_id=principal.org_id, kind="csv", updated_by_user_id=principal.user_id
        )
        session.add(connection)
        session.flush()
    if active_sync(session, connection):
        raise SyncError(409, "已有同步在进行")
    run = SyncRun(
        org_id=principal.org_id,
        connection_id=connection.id,
        requested_by_user_id=principal.user_id,
        input=input_data,
        counts={"records": 0, **IngestCounts().model_dump()},
    )
    session.add(run)
    _enqueue(session, run)
    session.commit()
    session.refresh(run)
    return public_run(run, kind)


def retry_run(session: Session, principal: Principal, run_id: UUID) -> SyncRunPublic:
    _lock_org(session, principal.org_id)
    run = session.exec(
        select(SyncRun)
        .where(
            SyncRun.org_id == principal.org_id,
            SyncRun.id == run_id,
        )
        .with_for_update()
    ).one_or_none()
    if run is None:
        raise SyncError(404, "同步记录不存在")
    connection = session.exec(
        select(SourceConnection)
        .where(
            SourceConnection.org_id == principal.org_id,
            SourceConnection.id == run.connection_id,
        )
        .with_for_update()
    ).one()
    if not (
        run.status == "failed"
        or (
            run.status == "running"
            and run.updated_at < utc_now() - timedelta(minutes=30)
        )
    ):
        raise SyncError(409, "这条同步当前不能重试")
    if (
        not session.connection()
        .execute(
            text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": str(connection.id)},
        )
        .scalar_one()
    ):
        raise SyncError(409, "已有同步在进行")
    if run.status == "failed" and active_sync(session, connection):
        raise SyncError(409, "已有同步在进行")
    run.status, run.trigger, run.error = "queued", "retry", None
    run.finished_at, run.updated_at = None, utc_now()
    session.add(run)
    _enqueue(session, run)
    session.commit()
    session.refresh(run)
    return public_run(run, connection.kind)


def jira_record(issue: JiraIssue) -> SourceRecord:
    return SourceRecord(
        source="jira",
        key=issue.key,
        url=issue.url,
        title=issue.summary,
        task_type=issue.issue_type,
        status=issue.status,
        created_at=issue.created,
        updated_at=issue.updated,
        resolved=issue.resolved,
        occurred_at=issue.resolved or issue.created,
        labels=issue.labels,
        components=issue.components,
        detail=issue.description or None,
        people=[
            SourcePerson(
                external_id=p.id,
                display_name=p.display_name,
                email=p.email,
                role=p.role,
                event_ref=p.role,
            )
            for p in issue.people
        ],
    )


def csv_record(row: WorkRecordRow) -> SourceRecord:
    people: dict[str, SourcePerson] = {}
    for index, person in enumerate([row.actor, *row.participants]):
        role = person.role or ("执行人" if index == 0 else "参与人")
        if person.account in people:
            people[person.account].role += "、" + role
        else:
            people[person.account] = SourcePerson(
                external_id=person.account,
                display_name=person.name or None,
                role=role,
                event_ref=person.account,
            )
    return SourceRecord(
        source=f"csv:{row.source}",
        key=row.record_id,
        title=row.title,
        task_type=row.record_type,
        occurred_at=row.occurred_at,
        project=row.project,
        detail=row.detail or None,
        status=row.status,
        people=list(people.values()),
    )


def _batch(
    session: Session,
    run: SyncRun,
    records: list[SourceRecord],
    cursor: dict[str, JsonValue],
) -> None:
    # Match account linking's lock order so a concurrent link sees the committed page.
    session.exec(
        select(ExternalIdentity)
        .where(
            ExternalIdentity.org_id == run.org_id,
            tuple_(col(ExternalIdentity.source), col(ExternalIdentity.external_id)).in_(
                {(r.source, p.external_id) for r in records for p in r.people}
            ),
        )
        .order_by(col(ExternalIdentity.id))
        .with_for_update()
    ).all()
    counts = dict(run.counts)
    for record in records:
        added = ingest_record(
            session,
            org_id=run.org_id,
            record=record,
            provenance={"sync_run_id": str(run.id)},
        )
        for key, value in added.model_dump().items():
            counts[key] = counts.get(key, 0) + value
        counts["records"] += 1
    run.counts, run.cursor, run.updated_at = counts, cursor, utc_now()
    session.add(run)
    session.commit()


def _ingest(
    session: Session, run: SyncRun, connection: SourceConnection, page_size: int
) -> None:
    if connection.kind == "csv":
        assert run.input is not None and isinstance(run.input["rows"], list)
        rows = run.input["rows"]
        start = int(str(run.cursor.get("next_row", 0)))
        while start < len(rows):
            batch = rows[start : start + 200]
            _batch(
                session,
                run,
                [csv_record(WorkRecordRow.model_validate(r)) for r in batch],
                {"next_row": start + len(batch)},
            )
            start += len(batch)
        return
    config = JiraConfig.model_validate(connection.config)
    if "since" not in run.cursor:
        since = (
            connection.synced_until - timedelta(days=1)
            if connection.synced_until
            else datetime.combine(config.start_date, time.min, UTC)
        )
        run.cursor = {
            "since": since.isoformat(),
            "next_cursor": None,
            "max_updated": None,
        }
        session.add(run)
        session.commit()
    since = datetime.fromisoformat(str(run.cursor["since"]))
    with jira_client_for(connection) as jira:
        while not run.cursor.get("complete"):
            cursor = run.cursor["next_cursor"]
            page = jira.search(
                since, str(cursor) if cursor is not None else None, page_size
            )
            maximum = run.cursor.get("max_updated")
            dates = [i.updated for i in page.issues]
            if maximum:
                dates.append(datetime.fromisoformat(str(maximum)))
            _batch(
                session,
                run,
                [jira_record(i) for i in page.issues],
                {
                    **run.cursor,
                    "next_cursor": page.next_cursor,
                    "max_updated": max(dates).isoformat() if dates else None,
                    "complete": page.next_cursor is None,
                },
            )
    maximum = run.cursor.get("max_updated")
    if maximum:
        connection.synced_until = datetime.fromisoformat(str(maximum))
        session.add(connection)


def _finish(
    session: Session, run: SyncRun, status: str, error: str | None = None
) -> None:
    run.status, run.error, run.updated_at = status, error, utc_now()
    run.finished_at = None if status == "queued" else utc_now()
    session.add(run)
    if status != "queued":
        details: dict[str, JsonValue] = {**run.counts}
        record_audit(
            session,
            org_id=run.org_id,
            actor_type=ActorKind.SYSTEM_WORKER,
            actor_id=None,
            action=f"sync_run.{status}",
            target_type="sync_run",
            target_id=str(run.id),
            details=details,
        )
    session.commit()


def execute_sync(
    principal: Principal, run_id: UUID, initial_attempts: int = 0, page_size: int = 100
) -> None:
    # A dedicated connection retains the session lock across page commits and releases it on exit.
    with engine.connect() as db_connection, Session(db_connection) as session:
        run = session.exec(
            select(SyncRun).where(
                SyncRun.org_id == principal.org_id,
                SyncRun.id == run_id,
            )
        ).one_or_none()
        if run is None or run.status not in {"queued", "running"}:
            return
        key = str(run.connection_id)
        locked = (
            session.connection()
            .execute(
                text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))"),
                {"key": key},
            )
            .scalar_one()
        )
        if not locked:
            raise JobRetry(RetryDecision(retry_in={"seconds": 30}))
        try:
            session.refresh(run)
            if run.status not in {"queued", "running"}:
                return
            connection = session.exec(
                select(SourceConnection).where(
                    SourceConnection.org_id == principal.org_id,
                    SourceConnection.id == run.connection_id,
                )
            ).one()
            run.status, run.attempts = "running", run.attempts + 1
            run.started_at, run.updated_at, run.error = utc_now(), utc_now(), None
            session.add(run)
            session.commit()
            try:
                _ingest(session, run, connection, page_size)
                _finish(session, run, "succeeded")
            except Exception as error:
                session.rollback()
                session.refresh(run)
                retry = (
                    isinstance(error, RetryableSourceError)
                    and run.attempts - initial_attempts <= 3
                )
                message = (
                    str(error)
                    if isinstance(
                        error,
                        (
                            SourceAuthError,
                            SourceUnreachable,
                            RetryableSourceError,
                            SecretUnavailable,
                        ),
                    )
                    else "同步失败，请检查数据和配置后重试"
                )
                _finish(session, run, "queued" if retry else "failed", message)
                if retry:
                    assert isinstance(error, RetryableSourceError)
                    delay = (
                        error.retry_after
                        if error.retry_after is not None
                        else 30 * 2 ** (run.attempts - initial_attempts - 1)
                    )
                    raise JobRetry(RetryDecision(retry_in={"seconds": delay})) from None
        finally:
            session.rollback()
            session.connection().execute(
                text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"),
                {"key": key},
            )
            session.commit()
