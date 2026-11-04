"""Shared Jira/CSV ingestion; the caller owns the transaction and source-run lock."""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from pydantic import (
    AliasChoices,
    AwareDatetime,
    BaseModel,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)
from sqlalchemy import or_, tuple_
from sqlmodel import Session, col, select

from app.domains.audit.models import ActorKind
from app.domains.evidence.models import (
    Evidence,
    EvidenceKind,
    EvidenceStatus,
    Visibility,
)
from app.domains.evidence.service import (
    append_evidence,
    retract_evidence,
    supersede_evidence,
)
from app.domains.org.models import ExternalIdentity
from app.domains.source.linking import auto_link_identities
from app.domains.work.models import Task, WorkEvent


class SourcePerson(BaseModel):
    external_id: str = Field(
        min_length=1, validation_alias=AliasChoices("external_id", "id")
    )
    display_name: str | None = Field(
        default=None, validation_alias=AliasChoices("display_name", "name")
    )
    email: str | None = None
    role: str
    event_ref: str = Field(min_length=1)


class SourceRecord(BaseModel):
    source: str = Field(pattern=r"^(jira|csv:.+)$")
    key: str = Field(min_length=1)
    url: str | None = None
    title: str
    task_type: str | None = None
    status: str | None = None
    created_at: AwareDatetime | None = None
    updated_at: AwareDatetime | None = None
    resolved: AwareDatetime | None = None
    occurred_at: AwareDatetime
    labels: list[str] = Field(default_factory=list)
    components: list[str] = Field(default_factory=list)
    project: str | None = None
    detail: str | None = None
    people: list[SourcePerson] = Field(default_factory=list)

    @field_validator("created_at", "updated_at", "resolved", "occurred_at")
    @classmethod
    def utc_time(cls, value: datetime | None) -> datetime | None:
        return value.astimezone(UTC) if value is not None else None

    @model_validator(mode="after")
    def unique_events(self) -> SourceRecord:
        if len({p.event_ref for p in self.people}) != len(self.people):
            raise ValueError("event_ref must be unique within a source record")
        return self


class IngestCounts(BaseModel):
    tasks_added: int = 0
    tasks_updated: int = 0
    events_added: int = 0
    events_updated: int = 0
    evidence_added: int = 0
    evidence_superseded: int = 0
    evidence_retracted: int = 0
    evidence_unchanged: int = 0
    people_unlinked: int = 0


def _comparable(content: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return {
        k: v
        for k, v in content.items()
        if k not in ("source_updated_at", "restored_from")
    }


def _derive(
    session: Session,
    *,
    org_id: UUID,
    task: Task,
    record: SourceRecord,
    provenance: dict[str, JsonValue],
    counts: IngestCounts,
) -> None:
    identities = {
        identity.external_id: identity
        for identity in session.exec(
            select(ExternalIdentity).where(
                ExternalIdentity.org_id == org_id,
                ExternalIdentity.source == record.source,
                col(ExternalIdentity.external_id).in_(
                    {p.external_id for p in record.people}
                ),
            )
        )
    }
    roles: dict[UUID, set[str]] = {}
    first_events: dict[UUID, UUID] = {}
    for person in record.people:
        identity = identities.get(person.external_id)
        if identity is None or identity.employee_id is None:
            counts.people_unlinked += 1
            continue
        employee_id = identity.employee_id
        roles.setdefault(employee_id, set()).add(person.role)
        source_ref = f"{record.key}:{person.event_ref}"
        event = session.exec(
            select(WorkEvent).where(
                WorkEvent.org_id == org_id,
                WorkEvent.source == record.source,
                WorkEvent.source_ref == source_ref,
            )
        ).one_or_none()
        values = {
            "actor_employee_id": employee_id,
            "task_id": task.id,
            "action": person.role,
            "occurred_at": record.occurred_at,
            "outcome": record.status,
        }
        if event is None:
            event = WorkEvent.model_validate(
                {
                    "org_id": org_id,
                    "source": record.source,
                    "source_ref": source_ref,
                    **values,
                }
            )
            session.add(event)
            counts.events_added += 1
        elif any(getattr(event, key) != value for key, value in values.items()):
            for key, value in values.items():
                setattr(event, key, value)
            counts.events_updated += 1
        first_events.setdefault(employee_id, event.id)
    session.flush()
    reference = select(Evidence).where(
        Evidence.org_id == org_id,
        Evidence.source == record.source,
        Evidence.source_ref == record.key,
    )
    active = {
        e.subject_employee_id: e
        for e in session.exec(
            reference.where(
                Evidence.status == EvidenceStatus.ACTIVE,
            )
        )
    }
    data = record.model_dump(mode="json")
    content = {
        key: data[key]
        for key in (
            "key",
            "url",
            "title",
            "status",
            "resolved",
            "occurred_at",
            "labels",
            "components",
            "project",
            "detail",
        )
    }
    content.update(
        type=record.task_type,
        created=data["created_at"],
        source_updated_at=data["updated_at"],
    )
    for employee_id, employee_roles in roles.items():
        body: dict[str, JsonValue] = {
            **content,
            "roles": cast(JsonValue, sorted(employee_roles)),
        }
        original = active.get(employee_id)
        if original is not None and _comparable(original.content) == _comparable(body):
            counts.evidence_unchanged += 1
            continue
        # CSV may have no updated_at, including when a correction returns to older content.
        while duplicate := session.exec(
            reference.where(
                Evidence.subject_employee_id == employee_id,
                col(Evidence.content) == body,
            )
        ).one_or_none():
            body["restored_from"] = str(duplicate.id)
        if original is None:
            _, created = append_evidence(
                session,
                org_id=org_id,
                subject_employee_id=employee_id,
                kind=EvidenceKind.WORK_RECORD,
                source=record.source,
                source_ref=record.key,
                title=record.title,
                content=body,
                visibility=Visibility.STANDARD,
                provenance=provenance,
                occurred_at=record.occurred_at,
                event_id=first_events[employee_id],
            )
            counts.evidence_added += int(created)
        else:
            supersede_evidence(
                session,
                original=original,
                content=body,
                title=record.title,
                occurred_at=record.occurred_at,
                event_id=first_events[employee_id],
                provenance=provenance,
                reason="来源记录已更新",
                actor_type=ActorKind.SYSTEM_WORKER,
                actor_id=None,
            )
            counts.evidence_superseded += 1
    if counts.people_unlinked == 0:
        for employee_id, evidence in active.items():
            if employee_id not in roles:
                retract_evidence(
                    session,
                    evidence=evidence,
                    reason="来源记录里已经没有这名员工",
                    actor_type=ActorKind.SYSTEM_WORKER,
                    actor_id=None,
                )
                counts.evidence_retracted += 1
    session.flush()


def prepare_identities(
    session: Session, org_id: UUID, records: Sequence[SourceRecord]
) -> IngestCounts:
    identities = {
        (identity.source, identity.external_id): identity
        for identity in session.exec(
            select(ExternalIdentity)
            .where(
                ExternalIdentity.org_id == org_id,
                tuple_(
                    col(ExternalIdentity.source), col(ExternalIdentity.external_id)
                ).in_({(r.source, p.external_id) for r in records for p in r.people}),
            )
            .order_by(col(ExternalIdentity.id))
            .with_for_update()
        )
    }
    for record in records:
        for person in record.people:
            key = record.source, person.external_id
            if key not in identities:
                identities[key] = ExternalIdentity(
                    org_id=org_id, source=record.source, external_id=person.external_id
                )
                session.add(identities[key])
            identity = identities[key]
            identity.display_name, identity.email = person.display_name, person.email
    linked = auto_link_identities(session, org_id, list(identities.values()))
    return rederive_identities(session, org_id=org_id, identities=linked)


def ingest_record(
    session: Session,
    *,
    org_id: UUID,
    record: SourceRecord,
    provenance: dict[str, JsonValue],
    identities_prepared: bool = False,
) -> IngestCounts:
    counts = (
        IngestCounts()
        if identities_prepared
        else prepare_identities(session, org_id, [record])
    )
    data = record.model_dump(mode="json")
    attributes = {
        key: data[key]
        for key in (
            "labels",
            "components",
            "project",
            "detail",
            "resolved",
            "occurred_at",
        )
    }
    attributes["people"] = [
        {
            "id": p.external_id,
            "name": p.display_name,
            "email": p.email,
            "role": p.role,
            "event_ref": p.event_ref,
        }
        for p in record.people
    ]
    values = {
        "title": record.title,
        "task_type": record.task_type,
        "status": record.status,
        "url": record.url,
        "source_created_at": record.created_at,
        "source_updated_at": record.updated_at,
        "attributes": attributes,
    }
    task = session.exec(
        select(Task)
        .where(
            Task.org_id == org_id,
            Task.source == record.source,
            Task.external_key == record.key,
        )
        .with_for_update()
    ).one_or_none()
    if task is None:
        task = Task.model_validate(
            {
                "org_id": org_id,
                "source": record.source,
                "external_key": record.key,
                **values,
            }
        )
        session.add(task)
        counts.tasks_added += 1
    elif any(getattr(task, key) != value for key, value in values.items()):
        for key, value in values.items():
            setattr(task, key, value)
        counts.tasks_updated += 1
    session.flush()
    _derive(
        session,
        org_id=org_id,
        task=task,
        record=record,
        provenance=provenance,
        counts=counts,
    )
    return counts


class IdentityNotFound(ValueError):
    """The identity does not belong to the supplied organization."""


def rederive_identity(
    session: Session, *, org_id: UUID, identity: ExternalIdentity
) -> IngestCounts:
    stored = session.exec(
        select(ExternalIdentity).where(
            ExternalIdentity.org_id == org_id,
            ExternalIdentity.id == identity.id,
        )
    ).one_or_none()
    if stored is None:
        raise IdentityNotFound("External identity not found in organization")
    return rederive_identities(session, org_id=org_id, identities=[stored])


def rederive_identities(
    session: Session, *, org_id: UUID, identities: Sequence[ExternalIdentity]
) -> IngestCounts:
    counts = IngestCounts()
    identities = [identity for identity in identities if identity.org_id == org_id]
    if not identities:
        return counts
    tasks = session.exec(
        select(Task)
        .where(
            Task.org_id == org_id,
            or_(
                *(
                    (col(Task.source) == identity.source)
                    & col(Task.attributes).contains(
                        {"people": [{"id": identity.external_id}]}
                    )
                    for identity in identities
                )
            ),
        )
        .order_by(col(Task.id))
        .with_for_update()
    ).all()
    for task in tasks:
        record = SourceRecord.model_validate(
            {
                **task.attributes,
                "source": task.source,
                "key": task.external_key,
                "title": task.title,
                "task_type": task.task_type,
                "status": task.status,
                "url": task.url,
                "created_at": task.source_created_at,
                "updated_at": task.source_updated_at,
                "people": task.attributes["people"],
            }
        )
        result = IngestCounts()
        _derive(
            session,
            org_id=org_id,
            task=task,
            record=record,
            provenance={"task_id": str(task.id), "reason": "identity_linked"},
            counts=result,
        )
        for field in IngestCounts.model_fields:
            setattr(counts, field, getattr(counts, field) + getattr(result, field))
    return counts
