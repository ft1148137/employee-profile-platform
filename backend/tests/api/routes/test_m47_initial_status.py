import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.domains.base import utc_now
from app.domains.capability.models import CapabilityDefinition, CapabilitySchemaVersion
from app.domains.change_request.models import (
    ChangeItem,
    ChangeOp,
    ProfileChangeRequest,
    RequestOrigin,
    RequestStatus,
    ReviewDecision,
    ReviewVia,
)
from app.domains.evidence.models import EvidenceKind, Visibility
from app.domains.evidence.service import append_evidence
from app.domains.profile.models import ProfileVersion
from tests.authz.test_tenant_access import headers
from tests.authz.test_tenant_access import tenant_data as tenant_data


@pytest.mark.parametrize(
    "origin,status,direct,records,expected",
    [
        ("manual", "pending_review", False, False, "waiting_source"),
        ("manual", "pending_review", False, True, "waiting_draft"),
        ("manual", "approved", False, True, "waiting_draft"),
        ("initial_sync", "pending_review", False, False, "owner_reviewed"),
        ("initial_sync", "withdrawn", False, False, "owner_reviewed"),
        ("manual", "approved", True, False, "direct_edited"),
        ("initial_sync", "pending_owner", True, False, "pending_owner"),
        ("initial_sync", "withdrawn", True, False, "owner_reviewed"),
    ],
)
def test_status_reflects_initial_request_not_supplement(
    tenant_data: tuple,
    db: Session,
    client: TestClient,
    origin: str,
    status: str,
    direct: bool,
    records: bool,
    expected: str,
) -> None:
    orgs, _, employees, users = tenant_data
    org_id = orgs[0].id
    employee = employees[2]
    schema = db.exec(
        select(CapabilitySchemaVersion).where(CapabilitySchemaVersion.org_id == org_id)
    ).one()
    capability = CapabilityDefinition(
        org_id=org_id, schema_version_id=schema.id, key="test", name="合成能力"
    )
    db.add(capability)
    request = ProfileChangeRequest(
        org_id=org_id,
        subject_employee_id=employee.id,
        origin=RequestOrigin(origin),
        status=RequestStatus(status),
        author={},
        submitted_at=utc_now() if status != "pending_owner" else None,
    )
    db.add(request)
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
    if records:
        append_evidence(
            db,
            org_id=org_id,
            subject_employee_id=employee.id,
            kind=EvidenceKind.WORK_RECORD,
            source="csv:test",
            source_ref="synthetic",
            title="合成工作记录",
            content={"text": "合成"},
            visibility=Visibility.STANDARD,
            provenance={},
        )
    if direct:
        edit = ProfileChangeRequest(
            org_id=org_id,
            subject_employee_id=employee.id,
            origin=RequestOrigin.MANUAL,
            status=RequestStatus.APPROVED,
            author={},
        )
        db.add(edit)
        db.flush()
        db.add(
            ReviewDecision(
                org_id=org_id,
                request_id=edit.id,
                reviewer_user_id=users[0].id,
                via=ReviewVia.DIRECT,
                request_revision=1,
            )
        )
    if direct or status == "approved":
        db.add(
            ProfileVersion(
                org_id=org_id,
                subject_employee_id=employee.id,
                revision=1,
                schema_version_id=schema.id,
                snapshot={},
                published_by={},
                published_at=utc_now(),
            )
        )
    db.commit()
    base = f"/api/v1/orgs/{org_id}/employees"
    for user in users[:3]:
        auth = headers(user)
        detail = client.get(f"{base}/{employee.id}", headers=auth)
        # The manager manages other departments; status must not widen access.
        if user.id == users[1].id:
            assert detail.status_code == 404
            continue
        assert detail.status_code == 200
        assert detail.json()["progress"]["initial_profile_status"] == expected
        page = client.get(base, headers=auth)
        assert page.status_code == 200
        summary = next(e for e in page.json()["data"] if e["id"] == str(employee.id))
        assert summary == detail.json()
    assert (
        client.get(f"{base}/{employee.id}", headers=headers(users[3])).status_code
        == 404
    )
