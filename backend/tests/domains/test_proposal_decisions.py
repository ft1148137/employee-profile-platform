from dataclasses import replace
from datetime import timedelta
from uuid import UUID

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.domains.admin_assistant import change_set as c
from app.domains.audit.models import AuditEvent
from app.domains.base import utc_now
from app.domains.capability.models import (
    CapabilityDefinition,
    CapabilityProposal,
    CapabilityProposalEvidence,
    ProposalStatus,
)
from app.domains.capability.proposals import (
    ProposalConflict,
    dismiss_proposal,
    list_proposals,
    name_key,
)
from app.domains.capability.publish import save_dictionary
from app.domains.capability.schemas import SaveDictionary
from app.domains.capability.service import read_editor
from app.domains.profile.models import ProfileClaim, ProfileVersion
from tests.admin_assistant.test_change_set import proposal
from tests.admin_assistant.test_registry import call, principal
from tests.domains.test_capability_proposals import evidence


def candidate(db: Session, demo: tuple, name: str = "合成新能力") -> CapabilityProposal:
    p = principal(db, demo)
    row = CapabilityProposal(
        org_id=p.org_id,
        name=name,
        name_key=name_key(name),
        category="工程",
        description="能力说明",
        reason="建议理由",
    )
    db.add(row)
    db.flush()
    for code in ("E02", "E02", "E03"):
        db.add(
            CapabilityProposalEvidence(
                org_id=p.org_id,
                proposal_id=row.id,
                evidence_id=evidence(db, p.org_id, demo[1][code].id),
            )
        )
    db.commit()
    return row


@pytest.mark.parametrize(
    "role,status",
    [("admin", 200), ("manager", 403), ("employee", 403), ("foreign", 404)],
)
def test_list_and_dismiss_permissions(
    db: Session, demo: tuple, client: TestClient, role: str, status: int
) -> None:
    row = candidate(db, demo)
    url = demo[0] + "/capabilities/proposals"
    response = client.get(url, headers=demo[2][role])
    assert response.status_code == status
    if role != "admin":
        assert (
            client.post(f"{url}/{row.id}/dismiss", headers=demo[2][role]).status_code
            == status
        )
        db.refresh(row)
        assert row.status == ProposalStatus.OPEN


def test_list_summary_and_today(db: Session, demo: tuple, client: TestClient) -> None:
    p = principal(db, demo)
    row = candidate(db, demo)
    newer = candidate(db, demo, "较新建议")
    newer.updated_at = utc_now() + timedelta(minutes=1)
    db.commit()
    data = list_proposals(db, p).data
    assert [i.id for i in data] == [newer.id, row.id]
    assert data[1].employee_count == 2 and data[1].evidence_count == 3
    assert data[1].evidence_titles == ["合成证据标题"] * 3
    summary = call(db, p, "list_capability_proposals")["data"]
    assert set(summary["data"][0]) == {
        "id",
        "name",
        "category",
        "description",
        "reason",
        "evidence_count",
        "updated_at",
    }
    assert "合成证据标题" not in str(summary) and "evidence_titles" not in str(summary)
    assert "display_name" not in str(data) and "content" not in str(summary)
    foreign = client.get("/api/v1/me/memberships", headers=demo[2]["foreign"]).json()[
        0
    ]["org"]["id"]
    assert not list_proposals(db, replace(p, org_id=UUID(foreign))).data
    with pytest.raises(HTTPException) as error:
        dismiss_proposal(db, replace(p, org_id=UUID(foreign)), row.id)
    assert error.value.status_code == 404

    def todos() -> list[dict]:
        return [
            t
            for t in client.get(
                demo[0] + "/admin/overview", headers=demo[2]["admin"]
            ).json()["todos"]
            if t["kind"] == "capability_proposals"
        ]

    assert todos()[0]["count"] == 2
    assert todos()[0]["page"] == "/admin/rules?tab=dictionary#dictionary-editor"
    for item in (row, newer):
        dismiss_proposal(db, p, item.id)
    assert not todos() and not list_proposals(db, p).data


def test_dismiss_conflict_audit_and_no_reappearance(
    db: Session, demo: tuple, client: TestClient
) -> None:
    p = principal(db, demo)
    row = candidate(db, demo)
    url = f"{demo[0]}/capabilities/proposals/{row.id}/dismiss"
    assert client.post(url, headers=demo[2]["admin"]).status_code == 204
    db.refresh(row)
    assert (
        row.status == "dismissed"
        and row.decided_by_user_id == p.user_id
        and row.decided_at
    )
    before = row.model_dump()
    assert client.post(url, headers=demo[2]["admin"]).status_code == 409
    db.refresh(row)
    assert row.model_dump() == before
    events = db.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == p.org_id, AuditEvent.target_id == str(row.id)
        )
    ).all()
    assert len(events) == 1 and events[0].action == "capability_proposal.dismissed"


def test_dictionary_accepts_only_on_commit_without_profile_writes(
    db: Session, demo: tuple
) -> None:
    p = principal(db, demo)
    row = candidate(db, demo, "新 能力")
    ignored = candidate(db, demo, "忽略能力")
    dismiss_proposal(db, p, ignored.id)
    editor = read_editor(db, p)
    counts = [
        len(db.exec(select(m).where(m.org_id == p.org_id)).all())
        for m in (ProfileClaim, ProfileVersion)
    ]
    definition = editor.definitions[0].model_copy(deep=True)
    definition.key, definition.name = "synthetic_new", "新能力"
    body = SaveDictionary(
        expected_version=editor.expected_version,
        definitions=[*editor.definitions, definition],
    )
    assert read_editor(db, p).expected_version == editor.expected_version
    save_dictionary(db, p, body, commit=False)
    db.flush()
    db.rollback()
    db.refresh(row)
    assert (
        row.status == "open"
        and read_editor(db, p).expected_version == editor.expected_version
    )
    save_dictionary(db, p, body)
    db.refresh(row)
    assert (
        row.status == "accepted"
        and row.decided_by_user_id == p.user_id
        and row.decided_at
    )
    assert read_editor(db, p).definitions[-1].name == "新能力"
    assert counts == [
        len(db.exec(select(m).where(m.org_id == p.org_id)).all())
        for m in (ProfileClaim, ProfileVersion)
    ]
    assert not db.exec(
        select(ProfileClaim).where(
            ProfileClaim.org_id == p.org_id,
            ProfileClaim.capability_definition_id
            == db.exec(
                select(CapabilityDefinition.id).where(
                    CapabilityDefinition.org_id == p.org_id,
                    CapabilityDefinition.key == definition.key,
                )
            ).one(),
        )
    ).all()
    db.refresh(ignored)
    assert ignored.status == "dismissed"


@pytest.mark.parametrize("collision", [False, True])
def test_assistant_undo_guards(db: Session, demo: tuple, collision: bool) -> None:
    p = principal(db, demo)
    row = candidate(db, demo)
    change = proposal(
        db,
        demo,
        items=[
            {"function": "dismiss_capability_proposal", "args": {"id": str(row.id)}}
        ],
    )
    assert row.status == "open" and change.items[0]["risk"] == "normal"
    assert change.items[0]["before"]["name"] == row.name
    assert c.apply(db, p, change.id, change.revision).status == "applied"
    if collision:
        other = CapabilityProposal(
            org_id=p.org_id,
            name=row.name,
            name_key=row.name_key,
            description="",
            reason="",
        )
        db.add(other)
    else:
        row.status = ProposalStatus.ACCEPTED
        db.add(row)
    db.commit()
    with pytest.raises(ProposalConflict, match="不能撤销"):
        c.undo(db, p, change.id)
    db.refresh(row)
    assert row.status == ("dismissed" if collision else "accepted")


def test_assistant_dismiss_undo_and_stale(db: Session, demo: tuple) -> None:
    p = principal(db, demo)
    row = candidate(db, demo)
    change = proposal(
        db,
        demo,
        items=[
            {"function": "dismiss_capability_proposal", "args": {"id": str(row.id)}}
        ],
    )
    c.apply(db, p, change.id, change.revision)
    assert c.undo(db, p, change.id).status == "applied"
    db.refresh(row)
    assert (
        row.status == "open"
        and row.decided_at is None
        and row.decided_by_user_id is None
    )
    events = db.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == p.org_id, AuditEvent.target_id == str(row.id)
        )
    ).all()
    assert {e.action for e in events} == {
        "capability_proposal.open",
        "capability_proposal.dismissed",
    }
    assert all(e.details["via"] == "admin_assistant" for e in events)
    stale = proposal(
        db,
        demo,
        items=[
            {"function": "dismiss_capability_proposal", "args": {"id": str(row.id)}}
        ],
    )
    dismiss_proposal(db, p, row.id)
    with pytest.raises(c.ChangeSetError, match="内容已变化"):
        c.apply(db, p, stale.id, stale.revision)


def test_reverse_cannot_restore_another_proposal(
    db: Session, demo: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = principal(db, demo)
    first = candidate(db, demo)
    other = candidate(db, demo, "另一建议")

    def args(row: CapabilityProposal) -> dict:
        return {
            "items": [
                {"function": "dismiss_capability_proposal", "args": {"id": str(row.id)}}
            ]
        }

    change = proposal(db, demo, **args(first))
    c.apply(db, p, change.id, change.revision)
    dismiss_proposal(db, p, other.id)
    with monkeypatch.context() as patch:
        patch.setattr(c, "apply", lambda d, p, id, revision: c.public(c.get(d, p, id)))
        reverse = c.undo(db, p, change.id)
    edited = c.propose(
        db,
        p,
        change.session_id,
        c.ProposeChanges.model_validate(args(other) | {"change_set_id": reverse.id}),
    )
    db.commit()
    with pytest.raises(c.ChangeSetError, match="撤销对象"):
        c.apply(db, p, edited.id, edited.revision)
    db.refresh(other)
    assert other.status == "dismissed"
