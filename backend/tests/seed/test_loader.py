import json
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from sqlmodel import Session, col, func, select

from app.core.config import settings
from app.core.db import engine
from app.domains.capability.models import CapabilityDefinition, CapabilitySchemaVersion
from app.domains.evidence.models import Evidence, EvidenceKind, Visibility
from app.domains.org.models import Employee, Organization, OrgMembership
from app.domains.profile.models import ClaimEvidence, ProfileClaim, ProfileVersion
from app.domains.profile.service import SnapshotError, build_profile_snapshot
from app.models import User
from app.seed.loader import load_demo
from app.seed.schema import SeedError, read_bundle


def test_import(seed_path: Path) -> None:
    with Session(engine) as session:
        first = load_demo(session, seed_path)
        second = load_demo(session, seed_path)
        assert first["skipped_jira_issues"] == ["PLAT-999"]
        assert all(c["added"] == 0 for c in second["counts"].values())
        assert first["counts"]["evidence"]["added"] == 54
        org = session.exec(
            select(Organization).where(
                Organization.slug == read_bundle(seed_path).org.slug
            )
        ).one()
        employees = session.exec(
            select(Employee).where(Employee.org_id == org.id)
        ).all()
        assert (
            session.exec(
                select(CapabilitySchemaVersion).where(
                    CapabilitySchemaVersion.org_id == org.id
                )
            )
            .one()
            .activated_at
        )
        assert all(
            user.full_name
            for user in session.exec(
                select(User)
                .join(OrgMembership, OrgMembership.user_id == User.id)
                .where(OrgMembership.org_id == org.id)
            ).all()
        )
        assert len(employees) == 12
        assert len({e.id for e in employees if e.display_name == "张伟"}) == 2
        evidence = session.exec(select(Evidence).where(Evidence.org_id == org.id)).all()
        assert all(
            e.visibility == Visibility.RESTRICTED
            for e in evidence
            if e.kind != EvidenceKind.WORK_RECORD
        )
        claims = session.exec(
            select(ProfileClaim).where(ProfileClaim.org_id == org.id)
        ).all()
        assert {c.assessment_status.value for c in claims} == {
            "confirmed",
            "tentative",
            "unknown",
            "disputed",
        }
        links = session.exec(
            select(ClaimEvidence).where(ClaimEvidence.org_id == org.id)
        ).all()
        assert {link.evidence_id for link in links} <= {e.id for e in evidence}
        versions = session.exec(
            select(ProfileVersion).where(ProfileVersion.org_id == org.id)
        ).all()
        assert len(versions) == 6 and max(v.revision for v in versions) == 2
        serialized = json.dumps([v.snapshot for v in versions], ensure_ascii=False)
        assert all(
            key not in serialized
            for key in ('"content"', '"provenance"', '"text"', '"comment"')
        )
        assert all(
            e.content.get("text", "not-in-snapshot") not in serialized
            for e in evidence
            if e.kind == EvidenceKind.SELF_REPORT
        )
        capabilities = session.exec(
            select(CapabilityDefinition)
            .where(CapabilityDefinition.org_id == org.id)
            .order_by(col(CapabilityDefinition.position))
        ).all()
        assert [c.key for c in capabilities] == [
            c.key for c in read_bundle(seed_path).capabilities
        ]
        with pytest.raises(SnapshotError):
            build_profile_snapshot(
                session,
                org_id=uuid4(),
                employee_id=employees[0].id,
                claim_ids=[claims[0].id],
            )


@pytest.mark.parametrize(
    ("filename", "field", "value"),
    [
        ("claims.yaml", "level", "not_sure"),
        ("claims.yaml", "evidence", [{"ref": "jira:missing", "relation": "supports"}]),
        ("claims.yaml", "supersedes", "missing"),
        ("profile_versions.yaml", "claims", ["missing"]),
        ("self_reports.yaml", "related_task_keys", ["missing"]),
        ("manager_reviews.yaml", "reviewer_employee_code", "missing"),
    ],
)
def test_invalid_file_is_atomic(
    seed_path: Path, filename: str, field: str, value: object
) -> None:
    file = seed_path / filename
    rows = yaml.safe_load(file.read_text())
    rows[0][field] = value
    file.write_text(yaml.safe_dump(rows))
    with Session(engine) as session:
        with pytest.raises(SeedError, match="(?s)" + filename + ".*" + field):
            load_demo(session, seed_path)
        slug = yaml.safe_load((seed_path / "org.yaml").read_text())["slug"]
        assert (
            session.exec(select(Organization).where(Organization.slug == slug)).first()
            is None
        )


def test_guard_and_rollback(seed_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with Session(engine) as session:
        monkeypatch.setattr(settings, "FASTAPI_ENV", None)
        with pytest.raises(SeedError, match="allow-non-dev"):
            load_demo(session, seed_path)
        monkeypatch.setattr(settings, "DEMO_USER_PASSWORD", None)
        with pytest.raises(SeedError, match="DEMO_USER_PASSWORD"):
            load_demo(session, seed_path, allow_non_dev=True)
        monkeypatch.setattr(settings, "DEMO_USER_PASSWORD", "test-password")
        monkeypatch.setattr(settings, "FIRST_SUPERUSER", "missing@example.com")
        with pytest.raises(SeedError, match="initial_data.py"):
            load_demo(session, seed_path, allow_non_dev=True)
        assert (
            session.exec(
                select(func.count())
                .select_from(Organization)
                .where(Organization.slug == read_bundle(seed_path).org.slug)
            ).one()
            == 0
        )


def test_demo_without_superuser(
    seed_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "FIRST_SUPERUSER", None)
    monkeypatch.setattr(settings, "FIRST_SUPERUSER_PASSWORD", None)
    monkeypatch.setattr(settings, "FASTAPI_ENV", None)
    with Session(engine) as session:
        result = load_demo(session, seed_path, allow_non_dev=True)
        assert (
            result["counts"]["user"]["added"] + result["counts"]["user"]["existing"]
            == 4
        )
