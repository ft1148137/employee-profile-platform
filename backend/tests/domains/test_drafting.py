from collections.abc import Callable
from uuid import UUID, uuid7

import pytest
from pydantic import ValidationError
from sqlmodel import Session

from app.authz.principal import Principal
from app.domains.capability.service import activate_template, list_capabilities
from app.domains.change_request.claim import ClaimAfter
from app.domains.change_request.models import (
    ChangeItem,
    ChangeItemEvidence,
    ProfileChangeRequest,
    RequestStatus,
)
from app.domains.drafting import model, service
from app.domains.drafting.collect import collect, evidence_stats, relation
from app.domains.evidence.models import Evidence
from app.domains.evidence.service import retract_evidence
from app.domains.model.client import ModelConfig
from app.domains.org.models import ExternalIdentity
from app.domains.profile.models import AssessmentStatus, EvidenceRelation, ProfileClaim
from app.worker.tasks import draft_initial
from app.worker.tenant import TenantNotFound
from tests.authz.test_tenant_access import tenant_data  # noqa: F401
from tests.domains.test_ingest import ingest, person, record, rows

pytestmark = pytest.mark.xdist_group("litellm")


def test_claim_after_timestamp() -> None:
    with pytest.raises(ValidationError):
        ClaimAfter(
            statement="x",
            assessment_status="tentative",
            latest_evidence_at="2026-01-01T00:00:00",
        )


def test_model_and_statistics(
    request: pytest.FixtureRequest,
    db: Session,
    mock_drafting: Callable[[UUID, UUID], None],
) -> None:
    orgs, _, employees, users = request.getfixturevalue("tenant_data")
    org, employee = orgs[0].id, employees[0]
    mock_drafting(org, users[0].id)
    principal = Principal.system_worker(org)
    schema = activate_template(db, principal, "tech-rnd")
    capabilities = list_capabilities(db, org, schema.id)
    db.add_all(
        [
            ExternalIdentity(
                org_id=org, employee_id=employee.id, source=s, external_id="alpha"
            )
            for s in ("jira", "csv:Git")
        ]
    )
    db.flush()
    for source in (
        record(
            person(), key="PUR-203", labels=["API"], occurred_at="2026-04-01T12:00:00Z"
        ),
        record(
            person(role="评审人、记录人"),
            source="csv:Git",
            key="GIT-1",
            labels=[],
            title="接口变更，对应PUR-203",
            occurred_at="2026-04-02T16:30:00Z",
        ),
        record(
            person(role="reporter"),
            key="PUR-204",
            labels=["api"],
            occurred_at="2026-05-01T00:00:00Z",
        ),
        record(person(role="reporter"), key="PUR-205", labels=["React"], components=[]),
        record(person(), key="PUR-206", labels=["RKNN"]),
    ):
        ingest(db, org, source)
    stale = next(e for e in rows(db, Evidence, org) if e.source_ref == "PUR-203")
    ingest(
        db,
        org,
        record(
            person(),
            key="PUR-203",
            title="Updated API record",
            occurred_at="2026-04-01T12:00:00Z",
        ),
    )
    revoked = next(e for e in rows(db, Evidence, org) if e.source_ref == "PUR-206")
    retract_evidence(
        db,
        evidence=revoked,
        reason="Synthetic correction",
        actor_type=principal.actor_type,
        actor_id=None,
    )
    records = collect(db, org, employee)
    assert not {stale.id, revoked.id} & {r.evidence.id for r in records}
    items, generated_by = model.draft(
        [r for r in records if r.evidence.source_ref != "PUR-205"],
        capabilities,
        ModelConfig("openai/gpt-4o-mini"),
    )
    assert len(items) == 1
    items = service.postprocess(db, org, employee, items, capabilities)
    item = items[0]
    assert item.after.evidence_count == 1 and item.after.source_count == 2
    assert item.after.latest_evidence_at.isoformat() == "2026-04-02T16:30:00+00:00"
    assert (
        item.after.level is None
        and item.after.assessment_status == AssessmentStatus.TENTATIVE
    )
    assert item.after.statement == "合成能力建议" and "2026-04-03。" in item.explanation
    assert "依据 1 项工作经历" in item.explanation and "2 个来源" in item.explanation
    assert (
        "Git" in item.explanation
        and "Jira" in item.explanation
        and item.uncertainty == "需要本人确认"
    )
    supports = [
        id_ for id_, rel in item.evidence.items() if rel == EvidenceRelation.SUPPORTS
    ]
    stats = evidence_stats(db, org, supports + [stale.id, revoked.id, uuid7()])
    assert (stats.evidence_count, stats.source_count, stats.latest_evidence_at) == (
        1,
        2,
        item.after.latest_evidence_at,
    )
    assert evidence_stats(db, orgs[1].id, supports).evidence_count == 0
    assert service.write_initial_request(db, org, employee, items, generated_by)
    change_request = rows(db, ProfileChangeRequest, org)[0]
    first_id = change_request.id
    assert change_request.revision == 1
    from app.domains.inbox.models import InboxItem

    notifications = rows(db, InboxItem, org)
    assert len(notifications) == 1 and notifications[0].kind == "initial_review"
    assert notifications[0].recipient_employee_id == employee.id
    assert change_request.base_version_id is not None and change_request.author == {
        "actor_type": "system_worker"
    }
    claim = ProfileClaim(
        org_id=org,
        subject_employee_id=employee.id,
        capability_definition_id=item.capability_id,
        statement="Published",
        assessment_status=AssessmentStatus.TENTATIVE,
        generated_by={},
    )
    db.add(claim)
    db.flush()
    assert service.write_initial_request(db, org, employee, items, generated_by)
    assert rows(db, ProfileChangeRequest, org)[0].id == first_id
    assert change_request.revision == 2
    assert len(rows(db, InboxItem, org)) == 1
    stored = rows(db, ChangeItem, org)
    assert (
        len(stored) == 1
        and stored[0].op == "modify"
        and stored[0].target_claim_id == claim.id
    )
    assert len(rows(db, ChangeItemEvidence, org)) == 3
    change_request.status = RequestStatus.PENDING_REVIEW
    db.flush()
    assert not service.write_initial_request(db, org, employee, [], {})
    assert len(rows(db, ChangeItem, org)) == 1
    with pytest.raises(service.InvalidDraft):
        service.write_initial_request(db, orgs[1].id, employee, [], {})
    item.after.level = "invalid"
    with pytest.raises(service.InvalidDraft):
        service.postprocess(db, org, employee, [item], capabilities)
    item.after.level = "routine"
    item.evidence = {uuid7(): EvidenceRelation.SUPPORTS}
    assert service.postprocess(db, org, employee, [item], capabilities) == []
    assert not collect(db, org, employees[-1])
    before = len(rows(db, ProfileChangeRequest, org))
    db.commit()
    draft_initial(org_id=str(org), employee_ids=[str(employees[-1].id)])
    assert len(rows(db, ProfileChangeRequest, org)) == before
    with pytest.raises(TenantNotFound):
        draft_initial(org_id=str(uuid7()))


@pytest.mark.parametrize("field", ["title", "detail"])
@pytest.mark.parametrize(
    "text,keys,count",
    [
        ("对应 PUR-203。", ["PUR-203"], 1),
        ("对应PUR-203。", ["PUR-203"], 1),
        ("见PLM-0012和FW-215", ["PLM-0012", "FW-215"], 1),
        ("PUR-203号需求", ["PUR-203"], 1),
        ("XPUR-203", ["PUR-203"], 2),
        ("1PUR-203", ["PUR-203"], 2),
        ("PUR-203x", ["PUR-203"], 2),
    ],
)
def test_record_reference_boundaries(
    request: pytest.FixtureRequest,
    db: Session,
    field: str,
    text: str,
    keys: list[str],
    count: int,
) -> None:
    orgs, _, employees, _ = request.getfixturevalue("tenant_data")
    org, employee = orgs[0].id, employees[0]
    db.add_all(
        ExternalIdentity(
            org_id=org, employee_id=employee.id, source=s, external_id="alpha"
        )
        for s in ("jira", "csv:Git")
    )
    db.flush()
    for key in keys:
        ingest(db, org, record(person(), key=key))
    ingest(
        db,
        org,
        record(person(), source="csv:Git", key="GIT-1", **{field: text}),
    )
    records = collect(db, org, employee)
    assert len(records) == len(keys) + 1
    stats = evidence_stats(db, org, [r.evidence.id for r in records])
    assert (stats.evidence_count, stats.source_count) == (count, 2)
    assert len({r.experience_id for r in records}) == count


@pytest.mark.usefixtures("mock_drafting")
def test_seed_fallback_and_transitive_groups(
    request: pytest.FixtureRequest,
    db: Session,
) -> None:
    from app.domains.evidence.models import EvidenceKind, Visibility
    from app.domains.evidence.service import append_evidence
    from app.domains.work.models import Task

    orgs, _, employees, _ = request.getfixturevalue("tenant_data")
    org, employee = orgs[0].id, employees[0]
    for source, key, title in [
        ("jira", "A-1", "API"),
        ("csv:Git", "B-2", "对应 A-1"),
        ("csv:PLM", "C-3", "对应 B-2"),
    ]:
        db.add(Task(org_id=org, source=source, external_key=key, title=title))
        append_evidence(
            db,
            org_id=org,
            subject_employee_id=employee.id,
            kind=EvidenceKind.WORK_RECORD,
            source=source,
            source_ref=key,
            title=title,
            content={},
            visibility=Visibility.STANDARD,
            provenance={},
        )
    db.flush()
    records = collect(db, org, employee)
    assert len({r.experience_id for r in records}) == 1
    assert all(relation(r) == EvidenceRelation.SUPPORTS for r in records)
    stats = evidence_stats(db, org, [r.evidence.id for r in records])
    assert stats.evidence_count == 1 and stats.latest_evidence_at is None
    schema = activate_template(db, Principal.system_worker(org), "tech-rnd")
    caps = list_capabilities(db, org, schema.id)
    items, _ = model.draft(records, caps, ModelConfig("openai/gpt-4o-mini"))
    items = service.postprocess(db, org, employee, items, caps)
    assert "最近一次的时间未知" in items[0].explanation
    assert records[0].task is not None
