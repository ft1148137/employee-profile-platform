# SPDX-License-Identifier: MIT

import csv
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlmodel import Session, select

from app.core.db import engine
from app.domains.audit.models import AuditEvent
from app.domains.evidence.models import Evidence, EvidenceKind, EvidenceStatus
from app.domains.org.models import Employee, ExternalIdentity, Organization
from app.domains.source.import_records import InvalidWorkRecords
from app.domains.work.ingest import IngestCounts, SourceRecord, ingest_record
from app.domains.work.models import Task, WorkEvent

ROOT = Path(__file__).resolve().parents[3]
SAMPLE = ROOT / "examples/connectors/sample_csv/work_records.csv"


def synchronize(session: Session, org_id: UUID, record: SourceRecord) -> IngestCounts:
    # Serialize this offline example without creating a production SourceConnection.
    session.exec(
        select(Organization).where(Organization.id == org_id).with_for_update()
    ).one()
    return ingest_record(
        session,
        org_id=org_id,
        record=record,
        provenance={"example": "sample_csv"},
    )


def test_sample_connector_sync(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.syspath_prepend(str(ROOT))
    from examples.connectors.sample_csv.connector import SampleCsvConnector

    (record,) = SampleCsvConnector(SAMPLE).read()
    assert record.source == "csv:sample_csv"
    assert record.key == "SAMPLE-001"
    assert record.occurred_at == datetime(2026, 1, 1, 1, tzinfo=UTC)
    assert [person.event_ref for person in record.people] == [
        "sample.lin",
        "sample.chen",
    ]

    with Session(engine) as session:
        org = Organization(slug=str(uuid4()), name="合成示例企业")
        other = Organization(slug=str(uuid4()), name="合成其他企业")
        session.add_all([org, other])
        session.flush()
        for tenant in (org, other):
            employee = Employee(
                org_id=tenant.id, display_name="示例林", work_email="lin@example.com"
            )
            session.add(employee)
            session.flush()
            session.add(
                ExternalIdentity(
                    org_id=tenant.id,
                    employee_id=employee.id,
                    source=record.source,
                    external_id="sample.lin",
                )
            )
        session.flush()

        assert synchronize(session, org.id, record) == IngestCounts(
            tasks_added=1, events_added=1, evidence_added=1, people_unlinked=1
        )
        task = session.exec(select(Task).where(Task.org_id == org.id)).one()
        event = session.exec(select(WorkEvent).where(WorkEvent.org_id == org.id)).one()
        original = session.exec(select(Evidence).where(Evidence.org_id == org.id)).one()
        assert task.external_key == record.key
        assert task.source == event.source == original.source == record.source
        assert event.source_ref == "SAMPLE-001:sample.lin"
        assert event.task_id == task.id and original.event_id == event.id
        assert original.kind == EvidenceKind.WORK_RECORD
        assert original.subject_employee_id == event.actor_employee_id
        assert original.provenance == {"example": "sample_csv"}
        assert original.content["roles"] == ["执行人"]
        assert original.content["detail"] == record.detail
        assert (
            not {"comments", "worklogs", "attachments", "people"}
            & original.content.keys()
        )
        assert not {"comments", "worklogs", "attachments"} & task.attributes.keys()
        unlinked = session.exec(
            select(ExternalIdentity).where(
                ExternalIdentity.org_id == org.id,
                ExternalIdentity.external_id == "sample.chen",
            )
        ).one()
        assert unlinked.employee_id is None
        for model in (Task, WorkEvent, Evidence):
            assert not session.exec(select(model).where(model.org_id == other.id)).all()

        before = original.model_dump()
        assert synchronize(session, org.id, record) == IngestCounts(
            evidence_unchanged=1, people_unlinked=1
        )
        assert session.exec(
            select(Evidence).where(Evidence.org_id == org.id)
        ).all() == [original]
        assert original.model_dump() == before

        # Read a corrected local file through the connector, keeping source keys stable.
        rows = list(csv.reader(StringIO(SAMPLE.read_text(encoding="utf-8"))))
        rows[1][rows[0].index("title")] = "示例：更正后的工作记录"
        output = StringIO(newline="")
        csv.writer(output).writerows(rows)
        corrected_file = tmp_path / "corrected.csv"
        corrected_file.write_text(output.getvalue(), encoding="utf-8")
        (corrected,) = SampleCsvConnector(corrected_file).read()
        assert synchronize(session, org.id, corrected).evidence_superseded == 1
        session.refresh(original)
        assert original.status == EvidenceStatus.SUPERSEDED
        assert original.content == before["content"]
        assert original.content_hash == before["content_hash"]
        assert original.title == before["title"]
        replacement = session.exec(
            select(Evidence).where(
                Evidence.org_id == org.id,
                Evidence.status == EvidenceStatus.ACTIVE,
            )
        ).one()
        assert replacement.supersedes_evidence_id == original.id
        assert replacement.title == corrected.title
        assert synchronize(session, org.id, corrected).evidence_unchanged == 1
        assert (
            len(session.exec(select(Evidence).where(Evidence.org_id == org.id)).all())
            == 2
        )
        assert session.exec(
            select(AuditEvent).where(
                AuditEvent.org_id == org.id,
                AuditEvent.action == "evidence.superseded",
            )
        ).all()
        session.rollback()


def test_sample_connector_rejects_invalid_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.syspath_prepend(str(ROOT))
    from examples.connectors.sample_csv.connector import SampleCsvConnector

    path = tmp_path / "invalid.csv"
    path.write_text("title\n缺少来源和账号\n", encoding="utf-8")
    with pytest.raises(InvalidWorkRecords):
        SampleCsvConnector(path).read()
