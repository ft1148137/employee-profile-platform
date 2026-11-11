import json
import logging
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, event, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.authz.principal import Principal
from app.core.db import engine
from app.domains.capability.models import (
    CapabilityProposal,
    CapabilityProposalEvidence,
    CapabilitySchemaVersion,
    ProposalStatus,
)
from app.domains.capability.proposals import store_proposals
from app.domains.capability.service import list_capabilities
from app.domains.change_request.models import ChangeItem, ProfileChangeRequest
from app.domains.drafting import incremental, model, service
from app.domains.evidence.service import append_evidence
from app.domains.profile.models import ProfileClaim, ProfileVersion
from tests.authz.test_tenant_access import tenant_data  # noqa: F401
from tests.domains.test_drafting_model import (
    answer,
    drafting,  # noqa: F401
)
from tests.domains.test_incremental_drafting import incremental_context  # noqa: F401
from tests.domains.test_ingest import ingest, person, record, rows
from tests.utils.migrations import migrate
from tests.utils.pi import PiHarness

pytestmark = pytest.mark.xdist_group("model")


def proposal(**changes: object) -> dict:
    return {
        "name": "新能力",
        "category": "工程",
        "description": "合成说明",
        "reason": "合成理由",
        "evidence": ["R1", "R2"],
        **changes,
    }


@pytest.mark.parametrize("is_incremental", [False, True])
def test_unknown_item_becomes_proposal_without_publishing(
    request: pytest.FixtureRequest,
    db: Session,
    pi_service: PiHarness,
    is_incremental: bool,
) -> None:
    context = request.getfixturevalue(
        "incremental_context" if is_incremental else "drafting"
    )
    org, schema, _, employees, _ = context[:5]
    ingest(db, org, record(person(), key="PROPOSAL-1"))
    db.commit()
    before = [c.model_dump() for c in list_capabilities(db, org, schema.id)]
    schema_versions = len(rows(db, CapabilitySchemaVersion, org))
    versions = len(rows(db, ProfileVersion, org))
    claims = len(rows(db, ProfileClaim, org))
    known = json.loads(answer())["items"][0]
    if is_incremental:
        known["op"] = "modify"
    unknown = known | {
        "capability_key": "新能力",
        "op": "add",
        "evidence": ["R1", "R2"],
    }
    pi_service.replies.append({"items": [unknown, known]})
    runner = incremental.run_incremental if is_incremental else service.run_drafting
    runner(Principal.system_worker(org), [str(employees[0].id)])
    db.expire_all()
    change = rows(db, ProfileChangeRequest, org)[0]
    assert change.status == "pending_owner"
    assert change.generated_by["dropped_items"] == change.generated_by["proposals"] == 1
    assert change.generated_by["prompt_version"] == (
        "incremental-draft-v2" if is_incremental else "initial-draft-v2"
    )
    assert "新能力" not in json.dumps(change.generated_by, ensure_ascii=False)
    assert len(rows(db, ChangeItem, org)) == 1
    suggestion = rows(db, CapabilityProposal, org)[0]
    assert suggestion.name == "新能力" and suggestion.status == "open"
    assert (
        suggestion.description == known["statement"]
        and suggestion.reason == known["explanation"]
    )
    assert suggestion.category is None
    assert len(rows(db, CapabilityProposalEvidence, org)) == 2
    assert before == [c.model_dump() for c in list_capabilities(db, org, schema.id)]
    assert len(rows(db, CapabilitySchemaVersion, org)) == schema_versions
    assert len(rows(db, ProfileVersion, org)) == versions
    assert len(rows(db, ProfileClaim, org)) == claims


@pytest.mark.parametrize(
    "bad",
    [
        {"evidence": ["R1"]},
        {"evidence": ["R1", "R999"]},
        {"evidence": ["R1", "R1"]},
        {"name": " Back end_API "},
        {"name": "x" * 41},
        {"reason": " "},
        {"level": "routine"},
        {"category": "x" * 41},
    ],
)
def test_bad_proposal_does_not_discard_items_or_good_proposal(
    request: pytest.FixtureRequest, db: Session, pi_service: PiHarness, bad: dict
) -> None:
    org, schema, _, employees, _ = request.getfixturevalue("drafting")
    existing = list_capabilities(db, org, schema.id)[0]
    pi_service.replies.append(
        {
            "items": json.loads(answer())["items"],
            "proposals": [
                proposal(**bad),
                proposal(name="有效建议"),
                proposal(name=existing.name),
            ],
        }
    )
    service.run_drafting(Principal.system_worker(org), [str(employees[0].id)])
    db.expire_all()
    assert len(rows(db, ChangeItem, org)) == 1
    assert [r.name for r in rows(db, CapabilityProposal, org)] == ["有效建议"]


def test_unknown_item_with_one_record_is_dropped(
    request: pytest.FixtureRequest, db: Session, pi_service: PiHarness
) -> None:
    org, _, _, employees, _ = request.getfixturevalue("drafting")
    pi_service.replies.append(answer(capability_key="missing"))
    service.run_drafting(Principal.system_worker(org), [str(employees[0].id)])
    db.expire_all()
    generated = rows(db, ProfileChangeRequest, org)[0].generated_by
    assert generated["dropped_items"] == 1 and generated["proposals"] == 0
    assert not rows(db, CapabilityProposal, org) and not rows(db, ChangeItem, org)


def test_excess_proposals_do_not_discard_valid_item(
    request: pytest.FixtureRequest, db: Session, pi_service: PiHarness
) -> None:
    org, _, _, employees, _ = request.getfixturevalue("drafting")
    pi_service.replies.append(
        {
            "items": json.loads(answer())["items"],
            "proposals": [
                proposal(evidence=["R1"]),
                *[
                    proposal(name=name)
                    for name in ("建议甲", "建议乙", "建议丙", "建议丁")
                ],
            ],
        }
    )
    service.run_drafting(Principal.system_worker(org), [str(employees[0].id)])
    db.expire_all()
    change = rows(db, ProfileChangeRequest, org)[0]
    assert change.status == "pending_owner"
    assert change.generated_by["proposals"] == 3
    items = rows(db, ChangeItem, org)
    assert len(items) == 1 and items[0].request_id == change.id
    assert {r.name for r in rows(db, CapabilityProposal, org)} == {
        "建议甲",
        "建议乙",
        "建议丙",
    }


def test_three_proposal_budget_includes_converted_items(
    request: pytest.FixtureRequest, db: Session, pi_service: PiHarness
) -> None:
    org, _, _, employees, _ = request.getfixturevalue("drafting")
    known = json.loads(answer())["items"][0]
    pi_service.replies.append(
        {
            "items": [
                known,
                *[
                    known | {"capability_key": name, "evidence": ["R1", "R2"]}
                    for name in ("转换甲", "转换乙")
                ],
            ],
            "proposals": [proposal(name=name) for name in ("显式甲", "显式乙")],
        }
    )
    service.run_drafting(Principal.system_worker(org), [str(employees[0].id)])
    db.expire_all()
    assert {r.name for r in rows(db, CapabilityProposal, org)} == {
        "显式甲",
        "显式乙",
        "转换甲",
    }
    generated = rows(db, ProfileChangeRequest, org)[0].generated_by
    assert generated["dropped_items"] == 2 and generated["proposals"] == 3


def evidence(db: Session, org: UUID, employee: UUID) -> UUID:
    row, _ = append_evidence(
        db,
        org_id=org,
        subject_employee_id=employee,
        kind="work_record",
        visibility="standard",
        source="csv:synthetic",
        source_ref=str(uuid4()),
        title="合成证据标题",
        content={},
        provenance={},
    )
    return row.id


def resolved(ids: list[UUID], **changes: object) -> model.DraftProposal:
    return model.DraftProposal(model.Proposal.model_validate(proposal(**changes)), ids)


def test_merge_deduplicates_caps_and_keeps_original_text(
    request: pytest.FixtureRequest, db: Session
) -> None:
    org, _, _, employees, _ = request.getfixturevalue("drafting")
    ids = [evidence(db, org, employees[0].id) for _ in range(12)]
    store_proposals(db, org, employees[0].id, [resolved(ids[:2], name=" S traße ")])
    db.commit()
    first = rows(db, CapabilityProposal, org)[0]
    updated = first.updated_at
    store_proposals(
        db,
        org,
        employees[0].id,
        [resolved(ids + ids, name="STRASSE", description="不得覆盖")],
    )
    db.commit()
    db.refresh(first)
    assert first.name == "S traße" and first.description == "合成说明"
    assert first.updated_at > updated
    assert len(rows(db, CapabilityProposal, org)) == 1
    assert len(rows(db, CapabilityProposalEvidence, org)) == 10
    first_ids = {r.evidence_id for r in rows(db, CapabilityProposalEvidence, org)}
    assert set(ids[:2]) <= first_ids
    store_proposals(db, org, employees[0].id, [resolved(ids, name="STRASSE")])
    db.commit()
    assert {
        r.evidence_id
        for r in rows(db, CapabilityProposalEvidence, org)
        if r.proposal_id == first.id
    } == first_ids


@pytest.mark.parametrize("status", [ProposalStatus.ACCEPTED, ProposalStatus.DISMISSED])
def test_terminal_name_is_never_written_again(
    request: pytest.FixtureRequest, db: Session, status: ProposalStatus
) -> None:
    org, _, _, employees, _ = request.getfixturevalue("drafting")
    ids = [evidence(db, org, employees[0].id) for _ in range(3)]
    store_proposals(db, org, employees[0].id, [resolved(ids[:2])])
    row = rows(db, CapabilityProposal, org)[0]
    row.status = status
    db.add(row)
    db.commit()
    updated = row.updated_at
    store_proposals(db, org, employees[0].id, [resolved(ids)])
    db.commit()
    assert len(rows(db, CapabilityProposal, org)) == 1
    assert len(rows(db, CapabilityProposalEvidence, org)) == 2
    assert row.updated_at == updated


def test_only_current_tenant_employee_standard_work_records(
    request: pytest.FixtureRequest, db: Session
) -> None:
    org, _, _, employees, _ = request.getfixturevalue("drafting")
    own = employees[0].id
    allowed = evidence(db, org, own)
    restricted, _ = append_evidence(
        db,
        org_id=org,
        subject_employee_id=own,
        kind="work_record",
        visibility="restricted",
        source="synthetic",
        source_ref="restricted",
        title="restricted",
        content={},
        provenance={},
    )
    self_report, _ = append_evidence(
        db,
        org_id=org,
        subject_employee_id=own,
        kind="self_report",
        visibility="standard",
        source="synthetic",
        source_ref="self",
        title="self",
        content={},
        provenance={},
    )
    foreign = evidence(db, employees[-1].org_id, employees[-1].id)
    other = evidence(db, org, employees[1].id)
    store_proposals(
        db,
        org,
        own,
        [resolved([allowed, restricted.id, self_report.id, foreign, other])],
    )
    assert not rows(db, CapabilityProposal, org)
    second = evidence(db, org, own)
    store_proposals(
        db,
        org,
        own,
        [resolved([allowed, second, restricted.id, self_report.id, foreign, other])],
    )
    db.commit()
    assert {r.evidence_id for r in rows(db, CapabilityProposalEvidence, org)} == {
        allowed,
        second,
    }
    row = rows(db, CapabilityProposal, org)[0]
    with pytest.raises(IntegrityError), db.begin_nested():
        db.add(
            CapabilityProposalEvidence(
                org_id=org, proposal_id=row.id, evidence_id=foreign
            )
        )
        db.flush()
    with pytest.raises(IntegrityError), db.begin_nested():
        row.decided_by_user_id = request.getfixturevalue("drafting")[4][3].id
        db.add(row)
        db.flush()


def test_savepoint_failure_preserves_draft_without_logging_content(
    request: pytest.FixtureRequest,
    db: Session,
    pi_service: PiHarness,
    caplog: pytest.LogCaptureFixture,
) -> None:
    org, _, _, employees, _ = request.getfixturevalue("drafting")

    def fail(_conn: object, _cursor: object, statement: str, *_args: object) -> None:
        if statement.startswith("INSERT INTO capability_proposal_evidence"):
            raise IntegrityError(
                "secret-evidence", {}, RuntimeError("合成敏感建议内容")
            )

    pi_service.replies.append(
        {
            "items": json.loads(answer())["items"],
            "proposals": [proposal(name="合成敏感建议内容")],
        }
    )
    event.listen(engine, "before_cursor_execute", fail)
    try:
        with caplog.at_level(
            logging.WARNING, logger="app.domains.capability.proposals"
        ):
            service.run_drafting(Principal.system_worker(org), [str(employees[0].id)])
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    db.expire_all()
    assert (
        len(rows(db, ChangeItem, org)) == len(rows(db, ProfileChangeRequest, org)) == 1
    )
    assert not rows(db, CapabilityProposal, org)
    assert not rows(db, CapabilityProposalEvidence, org)
    assert "IntegrityError" in caplog.text
    assert (
        "合成敏感建议内容" not in caplog.text and "secret-evidence" not in caplog.text
    )


def test_proposal_migration_roundtrip(migration_engine: Engine) -> None:
    migrate(migration_engine, "downgrade", "b308001bc001")
    assert "capability_proposal" not in inspect(migration_engine).get_table_names()
    migrate(migration_engine, "upgrade", "head")
    inspector = inspect(migration_engine)
    assert len(inspector.get_foreign_keys("capability_proposal_evidence")) == 2
    assert next(
        i
        for i in inspector.get_indexes("capability_proposal")
        if i["name"] == "uq_open_proposal_name"
    )["unique"]
    with migration_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM alembic_version")
            ).scalar_one()
            == 1
        )
