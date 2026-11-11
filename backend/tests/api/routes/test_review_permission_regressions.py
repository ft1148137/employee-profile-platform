from datetime import timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.security import create_access_token
from app.domains.admin_assistant import change_set, store
from app.domains.admin_assistant.models import AdminChangeSet
from app.domains.admin_assistant.schemas import ProposeChanges
from app.domains.audit.models import AuditEvent
from app.domains.base import utc_now
from app.domains.change_request.models import ItemDecision, ProfileChangeRequest
from app.domains.org.models import Employee, OrgMembership, Role, RoleGrant
from app.domains.profile.models import ClaimEvidence, EvidenceRelation, ProfileClaim
from app.domains.review import appeal, service
from app.domains.review.models import ReviewAppeal
from app.domains.review.publish import current_claims
from tests.api.routes.test_review_delegations import setup_delegation
from tests.api.routes.test_reviews import actor, manager, pending, values


@pytest.mark.parametrize("status", ["upheld", "reopened"])
def test_admin_cannot_resolve_own_appeal_or_propose_resolution(
    db: Session, demo: tuple, client: TestClient, status: str
) -> None:
    owner, request = pending(db, demo)
    service.decide(
        manager(db, demo),
        db,
        request.id,
        request.revision,
        values(request, ItemDecision.REJECTED),
    )
    member = db.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == owner.org_id,
            OrgMembership.user_id == owner.user_id,
        )
    ).one()
    db.add(RoleGrant(org_id=owner.org_id, membership_id=member.id, role=Role.ORG_ADMIN))
    db.commit()
    owner = actor(db, owner.org_id, "employee.platform@demo.example.com")
    base = f"{demo[0]}/appeals"
    headers = demo[2]["employee"]
    created = client.post(
        base,
        headers=headers,
        json={
            "request_id": str(request.id),
            "item_id": str(request.items[0].id),
            "reason": "合成申诉",
        },
    )
    assert created.status_code == 200
    appeal_id = UUID(created.json()["id"])
    assert any(
        row["id"] == str(appeal_id) for row in client.get(base, headers=headers).json()
    )
    conversation = store.create_session(db, owner)
    db.commit()
    original = db.get(ReviewAppeal, appeal_id).model_dump()
    before_audits = list(
        db.exec(select(AuditEvent.id).where(AuditEvent.org_id == owner.org_id))
    )
    before_requests = list(
        db.exec(
            select(ProfileChangeRequest.id).where(
                ProfileChangeRequest.org_id == owner.org_id
            )
        )
    )
    body = {"status": status, "reason": "合成处理说明"}
    denied = client.post(f"{base}/{appeal_id}/resolve", headers=headers, json=body)
    message = "不能处理自己提出的申诉，请由另一位企业管理员处理"
    assert denied.status_code == 403 and denied.json()["detail"] == message
    with pytest.raises(appeal.SelfAppealResolutionForbidden, match=message):
        change_set.propose(
            db,
            owner,
            conversation.id,
            ProposeChanges.model_validate(
                {
                    "items": [
                        {
                            "function": "resolve_appeal",
                            "args": {"appeal_id": str(appeal_id), **body},
                        }
                    ],
                }
            ),
        )
    db.expire_all()
    assert db.get(ReviewAppeal, appeal_id).model_dump() == original
    assert (
        list(db.exec(select(AuditEvent.id).where(AuditEvent.org_id == owner.org_id)))
        == before_audits
    )
    assert (
        list(
            db.exec(
                select(ProfileChangeRequest.id).where(
                    ProfileChangeRequest.org_id == owner.org_id
                )
            )
        )
        == before_requests
    )
    assert not db.exec(
        select(AdminChangeSet).where(AdminChangeSet.org_id == owner.org_id)
    ).all()
    accepted = client.post(
        f"{base}/{appeal_id}/resolve", headers=demo[2]["admin"], json=body
    )
    assert accepted.status_code == 200
    assert accepted.json()["appeal"]["status"] == status


@pytest.mark.parametrize("ending", ["approved", "expired"])
def test_delegate_manager_sees_only_subject_claims_while_review_is_pending(
    db: Session, demo: tuple, client: TestClient, ending: str
) -> None:
    owner, request = pending(db, demo)
    delegate = actor(db, owner.org_id, "employee.algorithm@demo.example.com")
    member = db.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == owner.org_id,
            OrgMembership.user_id == delegate.user_id,
        )
    ).one()
    department_id = db.get(Employee, delegate.employee_id).department_id
    db.add(
        RoleGrant(
            org_id=owner.org_id,
            membership_id=member.id,
            role=Role.DEPT_MANAGER,
            department_id=department_id,
        )
    )
    db.commit()
    delegate = actor(db, owner.org_id, "employee.algorithm@demo.example.com")
    assert (
        db.get(Employee, owner.employee_id).department_id
        not in delegate.managed_department_ids
    )
    original_detail = service.get_review(manager(db, demo), db, request.id)
    evidence = next(
        e for e in original_detail.items[0].evidence if e.visibility == "restricted"
    )
    claim = current_claims(db, owner.org_id, owner.employee_id)["backend_api"]
    other = db.exec(
        select(ProfileClaim)
        .join(Employee, Employee.id == ProfileClaim.subject_employee_id)
        .where(
            ProfileClaim.org_id == owner.org_id,
            Employee.org_id == owner.org_id,
            Employee.department_id != department_id,
            ProfileClaim.subject_employee_id != owner.employee_id,
        )
    ).first()
    assert other is not None
    db.add_all(
        [
            ClaimEvidence(
                org_id=owner.org_id,
                claim_id=id_,
                evidence_id=evidence.id,
                relation=EvidenceRelation.SUPPORTS,
            )
            for id_ in (claim.id, other.id)
        ]
    )
    db.commit()
    headers = {
        "Authorization": f"Bearer {create_access_token(delegate.user_id, expires_delta=timedelta(minutes=5))}"
    }
    url = f"{demo[0]}/evidence/{evidence.id}"
    assert client.get(url, headers=headers).status_code == 404
    row = setup_delegation(db, demo, delegate=delegate)
    detail = client.get(url, headers=headers)
    assert detail.status_code == 200
    assert detail.json()["content"] == evidence.content
    assert {c["claim_id"] for c in detail.json()["claims"]} == {str(claim.id)}
    review = service.get_review(delegate, db, request.id)
    assert (
        next(e for e in review.items[0].evidence if e.id == evidence.id).model_dump(
            mode="json"
        )
        == detail.json()
    )
    assert client.get(url, headers=demo[2]["foreign"]).status_code == 404
    if ending == "approved":
        service.decide(delegate, db, request.id, request.revision, values(request))
    else:
        row.ends_at = utc_now()
        db.add(row)
    db.commit()
    assert client.get(url, headers=headers).status_code == 404
