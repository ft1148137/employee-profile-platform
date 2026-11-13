from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.authz.principal import Principal
from app.core.db import engine
from app.domains.audit.models import ActorKind
from app.domains.capability.models import CapabilityDefinition, CapabilitySchemaVersion
from app.domains.change_request.models import (
    ChangeItem,
    ChangeItemEvidence,
    ChangeOp,
    ProfileChangeRequest,
    RequestOrigin,
    RequestStatus,
)
from app.domains.change_request.service import InvalidTransition, transition
from app.domains.evidence.models import EvidenceKind, Visibility
from app.domains.evidence.service import append_evidence
from app.domains.org.models import Employee, Organization
from app.domains.org.service import employee_summary, list_employees
from app.domains.profile.models import AssessmentStatus, ProfileClaim
from app.seed.loader import load_demo
from app.seed.schema import read_bundle


@pytest.mark.parametrize("source", RequestStatus)
@pytest.mark.parametrize("target", RequestStatus)
def test_transitions(source: RequestStatus, target: RequestStatus) -> None:
    allowed = {
        "draft": {"pending_review", "withdrawn"},
        "pending_owner": {"draft", "pending_review"},
        "pending_review": {"approved", "changes_requested", "rejected", "withdrawn"},
    }
    request = ProfileChangeRequest(
        org_id=uuid4(),
        subject_employee_id=uuid4(),
        origin=RequestOrigin.MANUAL,
        status=source,
        author={},
    )
    before = request.updated_at
    if target in allowed.get(source, set()):
        transition(request, target)
        assert request.status == target and request.updated_at >= before
    else:
        with pytest.raises(InvalidTransition):
            transition(request, target)
        assert request.status == source and request.updated_at == before


def test_review_priority_and_query_count(seed_path: Path) -> None:
    with Session(engine) as session:
        load_demo(session, seed_path)
        org = session.exec(
            select(Organization).where(
                Organization.slug == read_bundle(seed_path).org.slug
            )
        ).one()
        employees = {
            e.employee_code: e
            for e in session.exec(
                select(Employee).where(Employee.org_id == org.id)
            ).all()
        }
        claims = {
            c.generated_by["seed_ref"]: c
            for c in session.exec(
                select(ProfileClaim).where(ProfileClaim.org_id == org.id)
            ).all()
        }
        request = session.exec(
            select(ProfileChangeRequest).where(
                ProfileChangeRequest.org_id == org.id,
                ProfileChangeRequest.subject_employee_id == employees["E02"].id,
            )
        ).one()
        claim = claims["C02v2"]
        claim.assessment_status = AssessmentStatus.DISPUTED
        session.add(claim)
        other = ProfileChangeRequest(
            org_id=org.id,
            subject_employee_id=employees["E02"].id,
            origin=RequestOrigin.MANUAL,
            status=RequestStatus.PENDING_REVIEW,
            author={},
        )
        session.add(other)
        session.flush()

        def status() -> str:
            return employee_summary(
                session, employees["E02"], Principal.system_worker(org.id)
            ).progress.review_status

        assert status() == "pending_employee"
        request.status = RequestStatus.DRAFT
        session.add(request)
        assert status() == "pending_employee"
        request.status = RequestStatus.WITHDRAWN
        session.add(request)
        assert status() == "pending_manager"
        other.status = RequestStatus.APPROVED
        session.add(other)
        assert status() == "disputed"
        claim.assessment_status = AssessmentStatus.CONFIRMED
        session.add(claim)
        # A disputed superseded claim must not override its confirmed successor.
        claims["C02"].assessment_status = AssessmentStatus.DISPUTED
        session.add(claims["C02"])
        assert status() == "published"
        assert (
            employee_summary(
                session, employees["E12"], Principal.system_worker(org.id)
            ).progress.review_status
            == "none"
        )
        session.add_all(
            [Employee(org_id=org.id, display_name=f"Synthetic {i}") for i in range(8)]
        )
        session.flush()
        principal = Principal(ActorKind.USER, org.id, uuid4(), None, True, frozenset())
        counts = []
        queries = []

        def count(*args: object) -> None:
            queries.append(args)

        for limit in (3, 20):
            queries.clear()
            event.listen(engine, "before_cursor_execute", count)
            try:
                page = list_employees(
                    session, principal, department_id=None, q=None, skip=0, limit=limit
                )
            finally:
                event.remove(engine, "before_cursor_execute", count)
            assert len(page.data) == limit and page.count == 20
            counts.append(len(queries))
            assert all(e.progress is not None for e in page.data)
        assert counts == [3, 3]
        fresh = next(e for e in page.data if e.display_name.startswith("Synthetic"))
        assert fresh.progress.model_dump() == {
            "fill_status": "not_started",
            "inference_status": "pending",
            "evidence_quality": "unknown",
            "initial_profile_status": "no_records",
            "review_status": "none",
        }
        session.rollback()


def test_request_database_constraints(seed_path: Path) -> None:
    with Session(engine) as session:
        load_demo(session, seed_path)
        org = session.exec(
            select(Organization).where(
                Organization.slug == read_bundle(seed_path).org.slug
            )
        ).one()
        request = session.exec(
            select(ProfileChangeRequest).where(
                ProfileChangeRequest.org_id == org.id,
                ProfileChangeRequest.origin == RequestOrigin.INITIAL_SYNC,
            )
        ).one()
        item = session.exec(
            select(ChangeItem)
            .where(ChangeItem.org_id == org.id, ChangeItem.request_id == request.id)
            .order_by(col(ChangeItem.position))
        ).first()
        claim = session.exec(
            select(ProfileClaim).where(ProfileClaim.org_id == org.id)
        ).first()
        foreign = Organization(slug=str(uuid4()), name="Synthetic foreign")
        session.add(foreign)
        session.flush()
        schema = CapabilitySchemaVersion(org_id=foreign.id, version=1)
        employee = Employee(org_id=foreign.id, display_name="Foreign employee")
        session.add_all([schema, employee])
        session.flush()
        capability = CapabilityDefinition(
            org_id=foreign.id,
            schema_version_id=schema.id,
            key="foreign",
            name="Foreign capability",
        )
        session.add(capability)
        evidence, _ = append_evidence(
            session,
            org_id=foreign.id,
            subject_employee_id=employee.id,
            kind=EvidenceKind.WORK_RECORD,
            source="test",
            source_ref="1",
            title="Foreign",
            content={},
            visibility=Visibility.STANDARD,
            provenance={},
        )
        duplicate = ProfileChangeRequest(
            org_id=org.id,
            subject_employee_id=request.subject_employee_id,
            origin=RequestOrigin.INITIAL_SYNC,
            status=RequestStatus.DRAFT,
            author={},
        )
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(duplicate)
            session.flush()
        for changes in (
            {"capability_definition_id": capability.id},
            {"target_claim_id": claim.id},
            {"op": ChangeOp.MODIFY},
            {"after": None},
        ):
            with pytest.raises(IntegrityError), session.begin_nested():
                session.add(
                    ChangeItem(
                        **(item.model_dump() | {"id": uuid4(), "position": 9} | changes)
                    )
                )
                session.flush()
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(
                ChangeItemEvidence(
                    org_id=org.id,
                    item_id=item.id,
                    evidence_id=evidence.id,
                    relation="supports",
                )
            )
            session.flush()
        session.rollback()
