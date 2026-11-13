import pytest
from sqlmodel import Session, select

from app.authz.principal import Principal
from app.domains.base import utc_now
from app.domains.capability.models import CapabilityDefinition, CapabilitySchemaVersion
from app.domains.change_request.models import (
    ChangeItem,
    ChangeOp,
    ProfileChangeRequest,
    RequestOrigin,
    RequestStatus,
)
from app.domains.evidence.models import EvidenceKind, Visibility
from app.domains.evidence.service import append_evidence
from app.domains.org.models import ExternalIdentity
from app.domains.org.service import employee_summary, list_employees
from app.domains.profile.models import InferenceStatus, ProfileProgress
from app.domains.source.models import SourceConnection
from tests.authz.test_tenant_access import tenant_data as tenant_data


@pytest.mark.parametrize(
    "source,records,inference,request_status,items,expected",
    [
        (False, False, "pending", None, False, "waiting_source"),
        (True, False, "pending", None, False, "no_records"),
        (False, True, "pending", None, False, "waiting_draft"),
        (False, True, "running", None, False, "drafting"),
        (False, True, "failed", None, False, "failed"),
        (False, True, "generated", None, False, "no_judgment"),
        (False, True, "generated", "pending_owner", False, "no_judgment"),
        (False, True, "generated", "pending_owner", True, "pending_owner"),
        (False, True, "generated", "draft", True, "pending_owner"),
        (False, True, "failed", "pending_review", True, "done"),
        (False, True, "generated", "withdrawn", True, "done"),
        (False, True, "generated", "approved", True, "done"),
        (False, True, "generated", "rejected", True, "done"),
    ],
)
def test_initial_status(
    tenant_data: tuple,
    db: Session,
    source: bool,
    records: bool,
    inference: str,
    request_status: str | None,
    items: bool,
    expected: str,
) -> None:
    orgs, _, employees, users = tenant_data
    employee = employees[3]
    org_id = orgs[0].id
    if source:
        db.add(
            SourceConnection(org_id=org_id, kind="jira", updated_by_user_id=users[0].id)
        )
        # Linking an account without records must still report no_records.
        db.add(
            ExternalIdentity(
                org_id=org_id,
                employee_id=employee.id,
                source="jira",
                external_id="synthetic",
            )
        )
    # A foreign enterprise's records and connection must not affect the result.
    db.add(
        SourceConnection(org_id=orgs[1].id, kind="jira", updated_by_user_id=users[3].id)
    )
    for subject in [employees[5], *([employee] if records else [])]:
        append_evidence(
            db,
            org_id=subject.org_id,
            subject_employee_id=subject.id,
            kind=EvidenceKind.WORK_RECORD,
            source="csv:test",
            source_ref="record",
            title="Synthetic work",
            content={"text": "Synthetic"},
            visibility=Visibility.STANDARD,
            provenance={},
        )
    db.add(
        ProfileProgress(
            org_id=org_id,
            employee_id=employee.id,
            inference_status=InferenceStatus(inference),
        )
    )
    if request_status:
        request = ProfileChangeRequest(
            org_id=org_id,
            subject_employee_id=employee.id,
            origin=RequestOrigin.INITIAL_SYNC,
            status=RequestStatus(request_status),
            author={},
        )
        db.add(request)
        db.flush()
        if items:
            schema = db.exec(
                select(CapabilitySchemaVersion).where(
                    CapabilitySchemaVersion.org_id == org_id,
                )
            ).one()
            capability = CapabilityDefinition(
                org_id=org_id,
                schema_version_id=schema.id,
                key="test",
                name="Synthetic",
            )
            db.add(capability)
            db.flush()
            db.add(
                ChangeItem(
                    org_id=org_id,
                    request_id=request.id,
                    position=0,
                    op=ChangeOp.ADD,
                    capability_definition_id=capability.id,
                    after={"level": "l1"},
                )
            )
    db.commit()
    principal = Principal.system_worker(org_id)
    detail = employee_summary(db, employee, principal)
    page = list_employees(db, principal, department_id=None, q=None, skip=0, limit=100)
    assert detail.progress.initial_profile_status == expected
    assert next(e for e in page.data if e.id == employee.id) == detail
    assert (
        next(
            e for e in page.data if e.id == employees[0].id
        ).progress.initial_profile_status
        == "done"
    )


def test_submitted_manual_request_counts_as_handled(
    tenant_data: tuple, db: Session
) -> None:
    orgs, _, employees, _ = tenant_data
    employee = employees[3]
    db.add(
        ProfileChangeRequest(
            org_id=orgs[0].id,
            subject_employee_id=employee.id,
            origin=RequestOrigin.MANUAL,
            status=RequestStatus.PENDING_REVIEW,
            author={},
            submitted_at=utc_now(),
        )
    )
    db.commit()
    summary = employee_summary(db, employee, Principal.system_worker(orgs[0].id))
    assert summary.progress.initial_profile_status == "done"
