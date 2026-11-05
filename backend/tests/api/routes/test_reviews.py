from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import delete, text
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select

from app.authz.deps import get_org_principal
from app.authz.principal import Principal
from app.core.db import engine
from app.domains.capability.schemas import DictionaryDefinition
from app.domains.capability.service import get_schema, list_capabilities, save_version
from app.domains.change_request import employee
from app.domains.change_request.models import (
    ChangeItem,
    ItemDecision,
    ProfileChangeRequest,
    RequestStatus,
    ReviewDecision,
    ReviewVia,
)
from app.domains.change_request.schemas import ItemInput
from app.domains.change_request.service import review_statuses
from app.domains.evidence.models import Evidence, EvidenceKind, Visibility
from app.domains.evidence.service import append_evidence, read_evidence
from app.domains.inbox.models import InboxItem, InboxKind
from app.domains.org.models import (
    Department,
    Employee,
    OrgMembership,
    ReportingLine,
    Role,
    RoleGrant,
)
from app.domains.profile.models import ClaimEvidence, ProfileVersion
from app.domains.review import service
from app.domains.review.publish import current_claims
from app.domains.review.routing import reviewers_for
from app.domains.review.schemas import DecisionInput
from app.models import User


def actor(db: Session, org_id: UUID, email: str) -> Principal:
    user = db.exec(select(User).where(User.email == email)).one()
    return get_org_principal(org_id, user, db)


def pending(
    db: Session,
    demo: tuple,
    *,
    items: list[dict] | None = None,
    role: str = "employee.platform",
) -> tuple[Principal, employee.ChangeRequestPublic]:
    subject = demo[1]["E02" if role == "employee.platform" else "E01"]
    principal = actor(db, subject.org_id, f"{role}@demo.example.com")
    draft = employee.save_draft(
        principal,
        db,
        None,
        [
            ItemInput.model_validate(i)
            for i in (
                items
                or [
                    {
                        "op": "modify",
                        "capability_key": "backend_api",
                        "statement": "合成的更新判断",
                        "owner_statement": "员工确认的合成依据",
                        "level": "routine",
                    }
                ]
            )
        ],
        "web",
    )
    result = employee.submit(principal, db, draft.id, draft.revision, "web")
    db.commit()
    return principal, result


def values(
    request: employee.ChangeRequestPublic,
    decision: ItemDecision = ItemDecision.APPROVED,
) -> list[DecisionInput]:
    return [
        DecisionInput(item_id=i.id, decision=decision, reason="合成审批原因")
        for i in request.items
    ]


def manager(db: Session, demo: tuple) -> Principal:
    return actor(db, demo[1]["E01"].org_id, "manager.platform@demo.example.com")


def add_manager(db: Session, subject: Employee, department_id: UUID) -> OrgMembership:
    user = User(
        username=str(uuid4()), email=f"{uuid4()}@example.com", hashed_password="unused"
    )
    db.add(user)
    db.flush()
    member = OrgMembership(
        org_id=subject.org_id, user_id=user.id, employee_id=subject.id
    )
    db.add(member)
    db.flush()
    db.add(
        RoleGrant(
            org_id=subject.org_id,
            membership_id=member.id,
            role=Role.DEPT_MANAGER,
            department_id=department_id,
        )
    )
    db.commit()
    return member


def test_http_approve_freezes_snapshot_and_replaces_claim(
    demo: tuple, client: TestClient, db: Session
) -> None:
    owner, request = pending(db, demo)
    url, _, auth = demo
    original = current_claims(db, owner.org_id, owner.employee_id)["backend_api"]
    previous = db.exec(
        select(ProfileVersion)
        .where(
            ProfileVersion.org_id == owner.org_id,
            ProfileVersion.subject_employee_id == owner.employee_id,
        )
        .order_by(ProfileVersion.revision.desc())
    ).first()
    listed = client.get(f"{url}/reviews", headers=auth["manager"])
    assert listed.status_code == 200
    summary = next(r for r in listed.json()["data"] if r["id"] == str(request.id))
    assert (
        summary["owner_reviewed"]
        and summary["can_decide"]
        and summary["item_count"] == 1
    )
    detail = client.get(f"{url}/reviews/{request.id}", headers=auth["manager"]).json()
    assert detail["items"][0]["before"]["id"] == str(original.id)
    assert detail["items"][0]["after"]["owner_statement"] == "员工确认的合成依据"
    assert any(e["content"] for e in detail["items"][0]["evidence"])
    result = client.post(
        f"{url}/reviews/{request.id}/decisions",
        headers=auth["manager"],
        json={
            "revision": request.revision,
            "decisions": [d.model_dump(mode="json") for d in values(request)],
            "comment": "整体合成意见",
        },
    )
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["status"] == "approved" and data["returned_request_id"] is None
    db.expire_all()
    claim = current_claims(db, owner.org_id, owner.employee_id)["backend_api"]
    assert claim.id != original.id and claim.supersedes_claim_id == original.id
    assert (
        claim.assessment_status == "confirmed" and claim.statement == "合成的更新判断"
    )
    version = db.get(ProfileVersion, UUID(data["published_version_id"]))
    assert (
        version.revision == previous.revision + 1
        and version.supersedes_version_id == previous.id
    )
    assert version.published_by == {**claim.generated_by}
    assert version.published_by["via"] == "web"
    assert version.published_by["request_id"] == str(request.id)
    frozen = next(c for c in version.snapshot["claims"] if c["id"] == str(claim.id))
    assert frozen["evidence"] and all(
        set(e) == {"id", "kind", "title", "relation"} for e in frozen["evidence"]
    )
    item = db.get(ChangeItem, request.items[0].id)
    assert item.published_claim_id == claim.id and item.decision == "approved"
    notes = db.exec(
        select(Evidence).where(
            Evidence.org_id == owner.org_id, Evidence.source_ref == data["decision_id"]
        )
    ).one()
    assert (
        notes.kind == EvidenceKind.MANAGER_REVIEW
        and notes.visibility == Visibility.RESTRICTED
    )
    assert read_evidence(db, owner, notes.id).content["comment"] == "整体合成意见"
    assert db.get(ProfileChangeRequest, request.id).decided_at is not None
    inbox = db.exec(
        select(InboxItem).where(
            InboxItem.org_id == owner.org_id, InboxItem.request_id == request.id
        )
    ).all()
    assert any(i.kind == InboxKind.REVIEW_REQUESTED and i.done_at for i in inbox)
    assert any(
        i.kind == InboxKind.DECIDED and i.recipient_employee_id == owner.employee_id
        for i in inbox
    )


@pytest.mark.parametrize("mode", ["mixed", "rejected", "changes_requested"])
def test_mixed_and_all_return_or_reject(demo: tuple, db: Session, mode: str) -> None:
    owner, request = pending(
        db,
        demo,
        items=[
            {
                "op": "add",
                "capability_key": key,
                "statement": "合成能力",
                "owner_statement": f"合成依据 {key}",
                "uncertainty": "合成不确定说明",
            }
            for key in ("automated_testing", "observability", "technical_docs")
        ],
    )
    decisions = values(
        request,
        ItemDecision.REJECTED if mode == "rejected" else ItemDecision.CHANGES_REQUESTED,
    )
    if mode == "mixed":
        decisions[0].decision, decisions[2].decision = (
            ItemDecision.APPROVED,
            ItemDecision.REJECTED,
        )
    result = service.decide(
        manager(db, demo), db, request.id, request.revision, decisions
    )
    db.commit()
    assert result.status == ("approved" if mode == "mixed" else mode)
    assert bool(result.published_version_id) == (mode == "mixed")
    if mode != "rejected":
        draft = employee.my_requests(owner, db, "all")
        child = next(r for r in draft.data if r.id == result.returned_request_id)
        assert child.parent_request_id == request.id and child.status == "draft"
        assert len(child.items) == (1 if mode == "mixed" else 3)
        assert all(
            i.decision is None and i.decision_reason == "合成审批原因" and i.uncertainty
            for i in child.items
        )
        for item in child.items:
            original = next(
                i for i in request.items if i.capability.key == item.capability.key
            )
            assert {e.evidence_id for e in item.evidence} == {
                e.evidence_id for e in original.evidence
            }
    notes = db.exec(
        select(Evidence).where(
            Evidence.org_id == owner.org_id,
            Evidence.source_ref == str(result.decision_id),
        )
    ).one()
    assert len(notes.content["reasons"]) == 3


def test_admin_without_employee_is_fallback_and_access_expires(
    demo: tuple, db: Session, client: TestClient
) -> None:
    owner, request = pending(db, demo, role="manager.platform")
    admin = actor(db, owner.org_id, "admin@demo.example.com")
    assert admin.employee_id is None
    reviewer = reviewers_for(db, owner.org_id, owner.employee_id)
    assert admin.user_id in reviewer
    detail = service.get_review(admin, db, request.id)
    evidence_id = detail.items[0].evidence[-1].id
    assert detail.items[0].evidence[-1].content is not None
    assert read_evidence(db, admin, evidence_id).content is not None
    url, _, auth = demo
    inbox = client.get(f"{url}/me/inbox", headers=auth["admin"])
    assert inbox.status_code == 200
    notification = next(
        i for i in inbox.json()["data"] if i["request_id"] == str(request.id)
    )
    assert (
        notification["kind"] == "review_requested"
        and inbox.json()["pending_count"] >= 1
    )
    assert (
        client.post(
            f"{url}/me/inbox/{notification['id']}/read", headers=auth["admin"]
        ).status_code
        == 200
    )
    foreign_notification = db.exec(
        select(InboxItem).where(
            InboxItem.org_id == owner.org_id,
            InboxItem.recipient_employee_id == demo[1]["E02"].id,
        )
    ).first()
    assert foreign_notification is not None
    assert (
        client.post(
            f"{url}/me/inbox/{foreign_notification.id}/read", headers=auth["admin"]
        ).status_code
        == 404
    )
    response = client.post(
        f"{url}/reviews/{request.id}/decisions",
        headers=auth["admin"],
        json={
            "revision": request.revision,
            "decisions": [d.model_dump(mode="json") for d in values(request)],
        },
    )
    assert response.status_code == 200, response.text
    db.expire_all()
    decision = db.get(ReviewDecision, UUID(response.json()["decision_id"]))
    assert decision.reviewer_user_id == admin.user_id
    db.expire_all()
    assert db.get(InboxItem, UUID(notification["id"])).done_at is not None
    assert read_evidence(db, admin, evidence_id).content is None
    assert read_evidence(db, owner, evidence_id).content is not None
    history_url = f"{url}/employees/{owner.employee_id}/change-requests?status=all"
    admin_history = client.get(history_url, headers=auth["admin"]).json()["data"]
    owner_history = client.get(history_url, headers=auth["manager"]).json()["data"]
    assert (
        next(r for r in admin_history if r["id"] == str(request.id))["items"][0][
            "decision_reason"
        ]
        is None
    )
    assert (
        next(r for r in owner_history if r["id"] == str(request.id))["items"][0][
            "decision_reason"
        ]
        == "合成审批原因"
    )
    assert (
        client.get(f"{url}/reviews/{request.id}", headers=auth["admin"]).status_code
        == 403
    )


def test_routing_hierarchy_and_reporting_line_priority(
    demo: tuple, db: Session
) -> None:
    employees = demo[1]
    subject, direct, upper = employees["E02"], employees["E01"], employees["E04"]
    root = Department(org_id=subject.org_id, name="合成上级部门")
    db.add(root)
    db.flush()
    department = db.get(Department, subject.department_id)
    department.parent_department_id = root.id
    child = Department(
        org_id=subject.org_id, name="合成下级部门", parent_department_id=department.id
    )
    db.add(child)
    db.flush()
    subject.department_id = child.id
    upper_member = add_manager(db, upper, root.id)
    assert reviewers_for(db, subject.org_id, subject.id) == [manager(db, demo).user_id]
    assert reviewers_for(db, subject.org_id, direct.id) == [upper_member.user_id]
    db.add(
        ReportingLine(
            org_id=subject.org_id, employee_id=subject.id, manager_employee_id=upper.id
        )
    )
    db.commit()
    assert reviewers_for(db, subject.org_id, subject.id) == [upper_member.user_id]
    db.exec(
        delete(RoleGrant).where(
            RoleGrant.org_id == subject.org_id,
            RoleGrant.membership_id == upper_member.id,
        )
    )
    db.commit()
    assert reviewers_for(db, subject.org_id, subject.id) == [manager(db, demo).user_id]


@pytest.mark.parametrize("admin", [False, True])
def test_self_review_always_forbidden(
    demo: tuple, db: Session, client: TestClient, admin: bool
) -> None:
    owner, request = pending(db, demo, role="manager.platform")
    if admin:
        member = db.exec(
            select(OrgMembership).where(
                OrgMembership.org_id == owner.org_id,
                OrgMembership.user_id == owner.user_id,
            )
        ).one()
        db.add(
            RoleGrant(org_id=owner.org_id, membership_id=member.id, role=Role.ORG_ADMIN)
        )
        db.commit()
    url, _, auth = demo
    response = client.post(
        f"{url}/reviews/{request.id}/decisions",
        headers=auth["manager"],
        json={
            "revision": request.revision,
            "decisions": [d.model_dump(mode="json") for d in values(request)],
        },
    )
    assert response.status_code == 403


def test_missing_reviewer_visible_to_admin(demo: tuple, db: Session) -> None:
    owner, request = pending(db, demo, role="manager.platform")
    db.exec(
        delete(RoleGrant).where(
            RoleGrant.org_id == owner.org_id, RoleGrant.role == Role.ORG_ADMIN
        )
    )
    member = db.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == owner.org_id, OrgMembership.user_id == owner.user_id
        )
    ).one()
    db.add(RoleGrant(org_id=owner.org_id, membership_id=member.id, role=Role.ORG_ADMIN))
    db.commit()
    principal = manager(db, demo)
    assert reviewers_for(db, owner.org_id, owner.employee_id) == []
    summary = next(
        r
        for r in service.list_pending_reviews(principal, db).data
        if r.id == request.id
    )
    assert not summary.can_decide and summary.unavailable_reason == "没有可用的审批人"
    assert (
        db.get(ProfileChangeRequest, request.id).status == RequestStatus.PENDING_REVIEW
    )


def test_nonreviewers_and_foreign_tenant_are_denied(
    demo: tuple, db: Session, client: TestClient
) -> None:
    _, request = pending(db, demo)
    url, employees, auth = demo
    add_manager(db, employees["E03"], employees["E04"].department_id)
    unrelated = actor(
        db,
        employees["E01"].org_id,
        db.get(
            User,
            db.exec(
                select(OrgMembership.user_id).where(
                    OrgMembership.org_id == employees["E01"].org_id,
                    OrgMembership.employee_id == employees["E03"].id,
                )
            ).one(),
        ).email,
    )
    with pytest.raises(HTTPException) as error:
        service.get_review(unrelated, db, request.id)
    assert error.value.status_code == 403
    for role in ("employee", "foreign", "admin"):
        expected = 404 if role == "foreign" else 403
        assert (
            client.get(f"{url}/reviews/{request.id}", headers=auth[role]).status_code
            == expected
        )
        assert (
            client.post(
                f"{url}/reviews/{request.id}/decisions",
                headers=auth[role],
                json={
                    "revision": request.revision,
                    "decisions": [d.model_dump(mode="json") for d in values(request)],
                },
            ).status_code
            == expected
        )
    assert service.list_pending_reviews(unrelated, db).count == 0


@pytest.mark.parametrize("bad", ["revision", "missing", "duplicate", "reason"])
def test_invalid_decisions_leave_request_untouched(
    demo: tuple, db: Session, client: TestClient, bad: str
) -> None:
    _, request = pending(db, demo)
    body = {
        "revision": request.revision,
        "decisions": [d.model_dump(mode="json") for d in values(request)],
    }
    if bad == "revision":
        body["revision"] -= 1
    elif bad == "missing":
        body["decisions"] = []
    elif bad == "duplicate":
        body["decisions"] *= 2
    else:
        body["decisions"][0].update(decision="rejected", reason="  ")
    response = client.post(
        f"{demo[0]}/reviews/{request.id}/decisions",
        headers=demo[2]["manager"],
        json=body,
    )
    assert response.status_code == (409 if bad == "revision" else 422)
    db.expire_all()
    assert db.get(ProfileChangeRequest, request.id).status == "pending_review"
    assert not db.exec(
        select(ReviewDecision).where(ReviewDecision.request_id == request.id)
    ).all()


def test_jira_only_and_retract_and_attach(demo: tuple, db: Session) -> None:
    subject = demo[1]["E02"]
    evidence, _ = append_evidence(
        db,
        org_id=subject.org_id,
        subject_employee_id=subject.id,
        kind=EvidenceKind.WORK_RECORD,
        source="jira",
        source_ref="synthetic-review",
        title="合成 Jira 依据",
        content={"synthetic": True},
        visibility=Visibility.RESTRICTED,
        provenance={},
    )
    owner, request = pending(
        db,
        demo,
        items=[
            {
                "op": "add",
                "capability_key": "automated_testing",
                "statement": "仅 Jira 支持",
                "evidence_ids": [evidence.id],
            }
        ],
    )
    reviewer = manager(db, demo)
    assert service.get_review(reviewer, db, request.id).items[0].jira_only
    service.decide(reviewer, db, request.id, request.revision, values(request))
    db.commit()
    assert (
        current_claims(db, owner.org_id, owner.employee_id)[
            "automated_testing"
        ].assessment_status
        == "tentative"
    )
    original = current_claims(db, owner.org_id, owner.employee_id)["backend_api"]
    links = db.exec(
        select(ClaimEvidence).where(
            ClaimEvidence.org_id == owner.org_id, ClaimEvidence.claim_id == original.id
        )
    ).all()
    _, attach = pending(
        db,
        demo,
        items=[
            {
                "op": "attach_evidence",
                "capability_key": "backend_api",
                "statement": "补依据",
                "evidence_ids": [evidence.id],
            }
        ],
    )
    service.decide(reviewer, db, attach.id, attach.revision, values(attach))
    db.commit()
    claim = current_claims(db, owner.org_id, owner.employee_id)["backend_api"]
    assert claim.statement == original.statement and claim.level == original.level
    assert claim.assessment_status == original.assessment_status
    assert set(
        db.exec(
            select(ClaimEvidence.evidence_id).where(ClaimEvidence.claim_id == claim.id)
        ).all()
    ) == {e.evidence_id for e in links} | {evidence.id}
    _, retract = pending(
        db,
        demo,
        items=[{"op": "retract", "capability_key": "backend_api", "statement": "撤销"}],
    )
    service.decide(reviewer, db, retract.id, retract.revision, values(retract))
    db.commit()
    removed = current_claims(db, owner.org_id, owner.employee_id)["backend_api"]
    assert removed.assessment_status == "unknown" and removed.level is None
    assert not db.exec(
        select(ClaimEvidence).where(ClaimEvidence.claim_id == removed.id)
    ).all()


@pytest.mark.parametrize("change", ["removed_key", "level", "superseded"])
def test_obsolete_item_cannot_be_approved(
    demo: tuple, db: Session, change: str
) -> None:
    owner, request = pending(db, demo)
    reviewer = manager(db, demo)
    if change == "superseded":
        _, other = pending(db, demo)
        service.decide(reviewer, db, other.id, other.revision, values(other))
    else:
        schema = get_schema(db, owner.org_id)
        definitions = [
            DictionaryDefinition.model_validate(
                c.model_dump(
                    include={"key", "name", "category", "description", "level_rubric"}
                )
            )
            for c in list_capabilities(db, owner.org_id, schema.id)
        ]
        if change == "removed_key":
            definitions = [d for d in definitions if d.key != "backend_api"]
        else:
            for level in next(
                d for d in definitions if d.key == "backend_api"
            ).level_rubric.levels:
                level.key = f"changed_{level.key}"
        save_version(db, actor(db, owner.org_id, "admin@demo.example.com"), definitions)
    db.commit()
    detail = service.get_review(reviewer, db, request.id)
    assert not detail.items[0].approvable and detail.items[0].approval_blocked_reason
    with pytest.raises(HTTPException) as error:
        service.decide(reviewer, db, request.id, request.revision, values(request))
    assert error.value.status_code == 422
    db.rollback()
    result = service.decide(
        reviewer,
        db,
        request.id,
        request.revision,
        values(request, ItemDecision.CHANGES_REQUESTED),
    )
    db.commit()
    assert result.status == "changes_requested"


def test_concurrent_reviewers_only_one_decides(demo: tuple, db: Session) -> None:
    owner, request = pending(db, demo)
    member = add_manager(db, demo[1]["E03"], demo[1]["E02"].department_id)
    second = get_org_principal(owner.org_id, db.get(User, member.user_id), db)
    principals = [manager(db, demo), second]
    barrier = Barrier(2)
    db.commit()

    def run(principal: Principal) -> int:
        with Session(engine) as session:
            barrier.wait(timeout=15)
            try:
                service.decide(
                    principal, session, request.id, request.revision, values(request)
                )
                session.commit()
                return 200
            except HTTPException as exc:
                session.rollback()
                return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(run, principals)) == [200, 409]
    assert (
        len(
            db.exec(
                select(ReviewDecision).where(ReviewDecision.request_id == request.id)
            ).all()
        )
        == 1
    )


def test_publish_and_decision_rollback_together(
    demo: tuple, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, request = pending(db, demo)
    before = current_claims(db, owner.org_id, owner.employee_id)["backend_api"].id
    original = service.record_audit

    def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("synthetic transaction failure")

    monkeypatch.setattr(service, "record_audit", fail)
    with pytest.raises(RuntimeError, match="synthetic"):
        service.decide(
            manager(db, demo), db, request.id, request.revision, values(request)
        )
    db.rollback()
    assert db.get(ProfileChangeRequest, request.id).status == "pending_review"
    assert (
        current_claims(db, owner.org_id, owner.employee_id)["backend_api"].id == before
    )
    assert not db.exec(
        select(ReviewDecision).where(ReviewDecision.request_id == request.id)
    ).all()
    monkeypatch.setattr(service, "record_audit", original)
    service.decide(
        manager(db, demo),
        db,
        request.id,
        request.revision,
        values(request),
        via=ReviewVia.MCP,
        client_name="Synthetic MCP",
    )
    db.commit()


def test_publish_maps_current_dictionary_and_clears_old_dispute(
    demo: tuple, db: Session
) -> None:
    subject = demo[1]["E02"]
    old = current_claims(db, subject.org_id, subject.id)["backend_api"]
    old.assessment_status = "disputed"
    for existing in db.exec(
        select(ProfileChangeRequest).where(
            ProfileChangeRequest.org_id == subject.org_id,
            ProfileChangeRequest.subject_employee_id == subject.id,
        )
    ).all():
        existing.status = RequestStatus.WITHDRAWN
    db.commit()
    assert (
        review_statuses(db, subject.org_id, [subject.id], {subject.id})[subject.id]
        == "disputed"
    )
    owner, request = pending(db, demo)
    schema = get_schema(db, owner.org_id)
    definitions = [
        DictionaryDefinition.model_validate(
            c.model_dump(
                include={"key", "name", "category", "description", "level_rubric"}
            )
        )
        for c in list_capabilities(db, owner.org_id, schema.id)
    ]
    active = save_version(
        db, actor(db, owner.org_id, "admin@demo.example.com"), definitions
    )
    db.commit()
    result = service.decide(
        manager(db, demo), db, request.id, request.revision, values(request)
    )
    db.commit()
    new = current_claims(db, owner.org_id, owner.employee_id)["backend_api"]
    assert new.capability_definition_id != old.capability_definition_id
    assert (
        db.get(ProfileVersion, result.published_version_id).schema_version_id
        == active.id
    )
    assert (
        review_statuses(db, owner.org_id, [owner.employee_id], {owner.employee_id})[
            owner.employee_id
        ]
        == "published"
    )


def test_dictionary_cannot_change_between_validation_and_publication(
    demo: tuple, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, request = pending(db, demo)
    admin = actor(db, owner.org_id, "admin@demo.example.com")
    schema = get_schema(db, owner.org_id)
    definitions = [
        DictionaryDefinition.model_validate(
            c.model_dump(
                include={"key", "name", "category", "description", "level_rubric"}
            )
        )
        for c in list_capabilities(db, owner.org_id, schema.id)
    ]
    original_publish = service.publish

    def publish_while_dictionary_changes(
        session: Session,
        row: ProfileChangeRequest,
        items: list[ChangeItem],
        decision: ReviewDecision,
    ) -> ProfileVersion:
        with Session(engine) as concurrent:
            concurrent.execute(text("SET LOCAL lock_timeout = '100ms'"))
            with pytest.raises(OperationalError) as error:
                save_version(concurrent, admin, definitions)
            assert error.value.orig.sqlstate == "55P03"
            concurrent.rollback()
        return original_publish(session, row, items, decision)

    monkeypatch.setattr(service, "publish", publish_while_dictionary_changes)
    service.decide(manager(db, demo), db, request.id, request.revision, values(request))
    db.commit()
    save_version(db, admin, definitions)
    db.commit()


@pytest.mark.parametrize("state", ["disabled", "inactive", "no_employee"])
def test_review_candidates_require_active_account_and_employment(
    demo: tuple, db: Session, state: str
) -> None:
    subject = demo[1]["E02"]
    member = db.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == subject.org_id,
            OrgMembership.employee_id == demo[1]["E01"].id,
        )
    ).one()
    isolated_user = User(
        username=str(uuid4()), email=f"{uuid4()}@example.com", hashed_password="unused"
    )
    db.add(isolated_user)
    db.flush()
    member.user_id = isolated_user.id
    user_id = isolated_user.id
    if state == "disabled":
        db.get(User, user_id).is_active = False
    elif state == "inactive":
        demo[1]["E01"].employment_status = "inactive"
    else:
        member.employee_id = None
    db.commit()
    reviewers = reviewers_for(db, subject.org_id, subject.id)
    assert reviewers == sorted(set(reviewers))
    if state != "no_employee":
        assert user_id not in reviewers
        assert actor(db, subject.org_id, "admin@demo.example.com").user_id in reviewers
    else:
        assert reviewers == [user_id]
        _, request = pending(db, demo)
        principal = get_org_principal(subject.org_id, isolated_user, db)
        assert principal.employee_id is None
        detail = service.get_review(principal, db, request.id)
        assert detail.items
        from app.domains.inbox.service import my_inbox

        notifications = my_inbox(principal, db)
        assert any(i.request_id == request.id for i in notifications.data)
        result = service.decide(
            principal, db, request.id, request.revision, values(request)
        )
        db.commit()
        assert db.get(ReviewDecision, result.decision_id).reviewer_user_id == user_id
