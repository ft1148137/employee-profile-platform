import pytest
from sqlmodel import Session, select

from app.domains.audit.models import AuditEvent
from app.domains.evidence.models import Evidence
from app.domains.org.models import Organization
from app.domains.profile.models import ProfileClaim, ProfileVersion
from app.domains.profile.schemas import SnapshotClaim
from tests.utils.org import delete_test_organization


def test_demo_clones_keep_frozen_references_in_their_own_tenant(
    db: Session, demo: tuple, shared_demo: tuple
) -> None:
    person = demo[1]["E02"]
    shared_person = shared_demo[1]["E02"]
    assert person.id != shared_person.id
    assert person.org_id != shared_person.org_id
    claim_ids = set(
        db.exec(select(ProfileClaim.id).where(ProfileClaim.org_id == person.org_id))
    )
    evidence_ids = set(
        db.exec(select(Evidence.id).where(Evidence.org_id == person.org_id))
    )
    version = db.exec(
        select(ProfileVersion).where(
            ProfileVersion.org_id == person.org_id,
            ProfileVersion.subject_employee_id == person.id,
            ProfileVersion.revision == 2,
        )
    ).one()
    snapshot = version.snapshot["claims"]
    assert isinstance(snapshot, list) and snapshot
    claims = [SnapshotClaim.model_validate(claim) for claim in snapshot]
    assert {claim.id for claim in claims} <= claim_ids
    refs = {ref.evidence_id for claim in claims for ref in claim.evidence}
    assert refs and refs <= evidence_ids

    receipt = db.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == person.org_id, AuditEvent.action == "seed.loaded"
        )
    ).one()
    assert receipt.target_id == str(person.org_id)


def test_batch_cleanup_guards_the_canonical_demo(db: Session, demo: tuple) -> None:
    clone_id = demo[1]["E02"].org_id
    canonical_id = db.exec(
        select(Organization.id).where(Organization.slug == "demo")
    ).one()
    with pytest.raises(ValueError, match="Only a test-owned organization"):
        delete_test_organization(db, clone_id, canonical_id)
    assert db.get(Organization, clone_id) is not None
    assert db.get(Organization, canonical_id) is not None
