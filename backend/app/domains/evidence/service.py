import hashlib
import json
from datetime import datetime
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import Session, select

from app.domains.evidence.models import Evidence, EvidenceKind, Visibility


def append_evidence(
    session: Session,
    *,
    org_id: UUID,
    subject_employee_id: UUID,
    kind: EvidenceKind,
    source: str,
    source_ref: str,
    title: str,
    content: dict[str, JsonValue],
    visibility: Visibility,
    provenance: dict[str, JsonValue],
    occurred_at: datetime | None = None,
    event_id: UUID | None = None,
) -> tuple[Evidence, bool]:
    """Append idempotently, including concurrent writers; leave commit to the caller."""
    canonical = json.dumps(
        content,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    evidence = Evidence(
        org_id=org_id,
        subject_employee_id=subject_employee_id,
        kind=kind,
        source=source,
        source_ref=source_ref,
        title=title,
        content=content,
        visibility=visibility,
        provenance=provenance,
        occurred_at=occurred_at,
        event_id=event_id,
        content_hash=content_hash,
    )
    statement = (
        insert(Evidence)
        .values(**evidence.model_dump())
        .on_conflict_do_nothing(constraint="uq_evidence_content")
        .returning(Evidence)
    )
    inserted = session.scalars(statement).one_or_none()
    if inserted is not None:
        return inserted, True
    existing = session.exec(
        select(Evidence).where(
            Evidence.org_id == org_id,
            Evidence.source == source,
            Evidence.source_ref == source_ref,
            Evidence.content_hash == content_hash,
        )
    ).one()
    return existing, False
