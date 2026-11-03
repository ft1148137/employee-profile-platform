import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlmodel import Session, col, select

from app.domains.evidence.models import Evidence, EvidenceKind, EvidenceStatus
from app.domains.org.models import Employee
from app.domains.work.models import Task

type RecordKey = tuple[str, str]


@dataclass(frozen=True)
class CollectedRecord:
    evidence: Evidence
    task: Task | None
    experience_id: RecordKey


@dataclass(frozen=True)
class EvidenceStats:
    evidence_count: int
    source_count: int
    latest_evidence_at: datetime | None
    sources: tuple[str, ...]


def _collect(
    session: Session, org_id: UUID, evidence: list[Evidence]
) -> list[CollectedRecord]:
    # shortcut: Rebuild all tenant Task groups 2 + item-count times per employee; cache them when M4 adds incremental drafting.
    tasks = {
        (t.source, t.external_key): t
        for t in session.exec(select(Task).where(Task.org_id == org_id))
    }
    parents = {key: key for key in tasks}

    def root(key: RecordKey) -> RecordKey:
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    by_ref: dict[str, list[RecordKey]] = {}
    for key in tasks:
        by_ref.setdefault(key[1], []).append(key)
    for key, task in tasks.items():
        refs = re.findall(
            r"(?<![A-Za-z0-9])[A-Z][A-Z0-9]*-\d+(?![A-Za-z0-9])",
            f"{task.title} {task.attributes.get('detail') or ''}",
        )
        for ref in refs:
            for other in by_ref.get(ref, []):
                parents[root(key)] = root(other)
    return [
        CollectedRecord(e, tasks.get(key), root(key) if key in parents else key)
        for e in evidence
        for key in [(e.source, e.source_ref)]
    ]


def collect(
    session: Session, org_id: UUID, employee: Employee
) -> list[CollectedRecord]:
    evidence = list(
        session.exec(
            select(Evidence)
            .where(
                Evidence.org_id == org_id,
                Evidence.subject_employee_id == employee.id,
                Evidence.kind == EvidenceKind.WORK_RECORD,
                Evidence.status == EvidenceStatus.ACTIVE,
            )
            .order_by(col(Evidence.id))
        )
    )
    return _collect(session, org_id, evidence)


def evidence_stats(
    session: Session, org_id: UUID, evidence_ids: list[UUID]
) -> EvidenceStats:
    evidence = list(
        session.exec(
            select(Evidence).where(
                Evidence.org_id == org_id,
                col(Evidence.id).in_(evidence_ids),
                Evidence.kind == EvidenceKind.WORK_RECORD,
                Evidence.status == EvidenceStatus.ACTIVE,
            )
        )
    )
    records = _collect(session, org_id, evidence)
    sources = tuple(sorted({e.source for e in evidence}))
    return EvidenceStats(
        len({r.experience_id for r in records}),
        len(sources),
        max((e.occurred_at for e in evidence if e.occurred_at), default=None),
        sources,
    )
