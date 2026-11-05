from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select

from app.core.db import engine
from app.domains.audit.models import AuditEvent
from app.domains.base import utc_now
from app.domains.capability.service import get_schema, list_capabilities
from app.domains.change_request import employee
from app.domains.change_request.models import (
    ChangeItem,
    ProfileChangeRequest,
    RequestStatus,
    ReviewDecision,
)
from app.domains.change_request.schemas import ItemInput
from app.domains.evidence.models import Evidence
from app.domains.inbox.models import InboxItem, InboxKind
from app.domains.profile.models import ClaimEvidence, ProfileVersion
from app.domains.review import policy, service
from app.domains.review.models import ApprovalPolicy
from app.domains.review.publish import current_claims
from app.domains.review.schemas import DecisionInput
from tests.api.routes.test_reviews import actor, manager, pending
from tests.utils.migrations import migrate


def save(client: TestClient, demo: tuple, **values: object) -> dict:
    response = client.put(
        f"{demo[0]}/approval-policy", headers=demo[2]["admin"], json=values
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_policy_versions_audit_and_authorization(
    demo: tuple, db: Session, client: TestClient
) -> None:
    url, employees, auth = demo
    base = f"{url}/approval-policy"
    initial = client.get(base, headers=auth["admin"]).json()
    assert initial["current"]["version"] == 0
    assert (
        initial["current"]["require_manager"]
        and initial["current"]["owner_review_days"] == 14
    )
    category = initial["categories"][0]
    first = save(
        client,
        demo,
        require_manager=False,
        category_overrides={category: True},
        owner_review_days=3,
    )
    second = save(client, demo, owner_review_days=90)
    assert [v["version"] for v in second["history"]] == [2, 1]
    assert second["history"][1] == first["current"]
    assert set(first["current"]["changed_fields"]) == {
        "require_manager",
        "category_overrides",
        "owner_review_days",
    }
    assert first["current"]["created_by_name"] and first["current"]["created_at"]
    rows = db.exec(
        select(ApprovalPolicy).where(ApprovalPolicy.org_id == employees["E02"].org_id)
    ).all()
    audits = db.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == employees["E02"].org_id,
            AuditEvent.action == "approval_policy.saved",
        )
    ).all()
    assert len(rows) == len(audits) == 2
    for role, status in [("manager", 403), ("employee", 403), ("foreign", 404)]:
        assert client.get(base, headers=auth[role]).status_code == status
        assert client.put(base, headers=auth[role], json={}).status_code == status
    for body in (
        {"owner_review_days": 0},
        {"owner_review_days": 91},
        {"owner_review_days": 1.5},
        {"category_overrides": {"unknown": False}},
        {"require_manager": "false"},
        {"category_overrides": {category: "false"}},
    ):
        assert client.put(base, headers=auth["admin"], json=body).status_code == 422
    assert client.get(base, headers=auth["admin"]).json()["current"]["version"] == 2


@pytest.mark.parametrize(
    "category,expected", [(None, True), ("", True), ("关闭", False), ("其他", True)]
)
def test_requires_manager(category: str | None, expected: bool) -> None:
    assert (
        policy.requires_manager(
            policy.PolicyInput(category_overrides={"关闭": False}), category
        )
        == expected
    )
    assert (
        policy.requires_manager(policy.PolicyInput(require_manager=False), category)
        is False
    )


@pytest.mark.parametrize("via", ["web", "mcp", "import", "bot"])
def test_owner_submission_publishes_tentative_without_manager(
    demo: tuple, db: Session, client: TestClient, via: employee.EmployeeVia
) -> None:
    save(client, demo, require_manager=False)
    owner = actor(db, demo[1]["E02"].org_id, "employee.platform@demo.example.com")
    draft = employee.save_draft(
        owner,
        db,
        None,
        [
            ItemInput(
                op="modify",
                capability_key="backend_api",
                statement="合成自述",
                owner_statement="已确认的说明",
            )
        ],
        via,
    )
    original = current_claims(db, owner.org_id, owner.employee_id)["backend_api"]
    result = employee.submit(owner, db, draft.id, draft.revision, via, "合成客户端")
    db.commit()
    assert result.status == "approved" and result.decided_at
    claim = current_claims(db, owner.org_id, owner.employee_id)["backend_api"]
    assert (
        claim.supersedes_claim_id == original.id
        and claim.assessment_status == "tentative"
    )
    assert result.items[0].published_claim_id == claim.id
    decision = db.exec(
        select(ReviewDecision).where(
            ReviewDecision.org_id == owner.org_id,
            ReviewDecision.request_id == result.id,
        )
    ).one()
    assert decision.via == "policy" and decision.reviewer_user_id is None
    assert not db.exec(
        select(InboxItem).where(
            InboxItem.org_id == owner.org_id,
            InboxItem.request_id == result.id,
            InboxItem.kind == InboxKind.REVIEW_REQUESTED,
        )
    ).all()
    assert db.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == owner.org_id,
            AuditEvent.target_id == str(result.id),
            AuditEvent.action == "change_request.policy_published",
        )
    ).one()
    response = client.post(
        f"{demo[0]}/me/requests/{result.id}/withdraw", headers=demo[2]["employee"]
    )
    assert response.status_code == 409


@pytest.mark.parametrize(
    "finish", ["approved", "rejected", "changes_requested", "withdrawn"]
)
def test_mixed_request_preserves_published_items(
    demo: tuple, db: Session, client: TestClient, finish: str
) -> None:
    org_id = demo[1]["E02"].org_id
    definitions = list_capabilities(db, org_id, get_schema(db, org_id).id)
    backend = next(c for c in definitions if c.key == "backend_api")
    other = next(
        c for c in definitions if c.category and c.category != backend.category
    )
    save(client, demo, category_overrides={backend.category: False})
    owner = actor(db, org_id, "employee.platform@demo.example.com")
    current = current_claims(db, org_id, owner.employee_id)
    previous = current["backend_api"]
    draft = employee.save_draft(
        owner,
        db,
        None,
        [
            ItemInput(
                op="modify",
                capability_key="backend_api",
                statement="自动发布项",
                owner_statement="发布项依据",
            ),
            ItemInput(
                op="modify" if other.key in current else "add",
                capability_key=other.key,
                statement="需要审批项",
                owner_statement="待审批项依据",
            ),
        ],
        "web",
    )
    result = employee.submit(owner, db, draft.id, draft.revision, "web")
    db.commit()
    assert result.status == "pending_review"
    assert [i.decision for i in result.items] == ["approved", None]
    published = current_claims(db, org_id, owner.employee_id)["backend_api"]
    assert published.supersedes_claim_id == previous.id
    policy_decision = (
        db.exec(
            select(ChangeItem).where(
                ChangeItem.org_id == org_id, ChangeItem.id == result.items[0].id
            )
        )
        .one()
        .review_decision_id
    )
    detail = service.get_review(manager(db, demo), db, result.id)
    assert [i.after.id for i in detail.items] == [result.items[1].id]
    # New rules cannot change the decision of an already submitted request.
    save(client, demo, require_manager=False)
    if finish == "withdrawn":
        employee.withdraw(owner, db, result.id)
        db.commit()
        links = db.exec(
            select(ClaimEvidence).where(
                ClaimEvidence.org_id == org_id, ClaimEvidence.claim_id == published.id
            )
        ).all()
        assert links and all(
            db.get(Evidence, link.evidence_id).status == "active" for link in links
        )
    else:
        decided = service.decide(
            manager(db, demo),
            db,
            result.id,
            result.revision,
            [
                DecisionInput(
                    item_id=result.items[1].id, decision=finish, reason="合成原因"
                )
            ],
        )
        db.commit()
        assert decided.status == "approved"
        assert bool(decided.published_version_id) == (finish == "approved")
        assert bool(decided.returned_request_id) == (finish == "changes_requested")
        if finish == "approved":
            assert (
                current_claims(db, org_id, owner.employee_id)[
                    other.key
                ].assessment_status
                == "confirmed"
            )
    db.expire_all()
    assert (
        current_claims(db, org_id, owner.employee_id)["backend_api"].id == published.id
    )
    assert db.get(ChangeItem, result.items[0].id).review_decision_id == policy_decision


def test_policy_does_not_change_pending_or_admin_handover(
    demo: tuple, db: Session, client: TestClient
) -> None:
    owner, submitted = pending(db, demo)
    save(client, demo, require_manager=False, owner_review_days=2)
    initial = db.exec(
        select(ProfileChangeRequest).where(
            ProfileChangeRequest.org_id == owner.org_id,
            ProfileChangeRequest.subject_employee_id == owner.employee_id,
            ProfileChangeRequest.origin == "initial_sync",
        )
    ).one()
    initial.created_at = utc_now() - timedelta(days=1, hours=23)
    db.commit()
    base = f"{demo[0]}/initial-profiles/hand-over"
    assert client.get(base, headers=demo[2]["admin"]).json()["count"] == 0
    initial.created_at -= timedelta(hours=2)
    db.commit()
    assert client.post(base, headers=demo[2]["admin"]).json()["count"] == 1
    db.refresh(initial)
    db.expire_all()
    assert (
        initial.status == "pending_review" and initial.submitted_via == "admin_handover"
    )
    assert (
        db.get(ProfileChangeRequest, submitted.id).status
        == RequestStatus.PENDING_REVIEW
    )
    assert not db.exec(
        select(ReviewDecision).where(ReviewDecision.org_id == owner.org_id)
    ).all()
    notice = db.exec(
        select(InboxItem).where(
            InboxItem.org_id == owner.org_id,
            InboxItem.request_id == initial.id,
            InboxItem.kind == "submitted",
        )
    ).one()
    assert "满 2 天" in notice.message


def test_policy_publication_rolls_back_on_failure(
    demo: tuple, db: Session, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.domains.review import publish as publishing

    save(client, demo, require_manager=False)
    owner = actor(db, demo[1]["E02"].org_id, "employee.platform@demo.example.com")
    draft = employee.save_draft(
        owner,
        db,
        None,
        [
            ItemInput(
                op="modify", capability_key="backend_api", statement="合成事务测试"
            )
        ],
        "web",
    )
    db.commit()
    versions = list(
        db.exec(
            select(ProfileVersion.id).where(ProfileVersion.org_id == owner.org_id)
        ).all()
    )
    original = publishing.publish

    def fail(*args: object, **kwargs: object) -> None:
        original(*args, **kwargs)
        raise RuntimeError("Synthetic publication failure")

    monkeypatch.setattr(publishing, "publish", fail)
    with pytest.raises(RuntimeError, match="Synthetic"):
        client.post(
            f"{demo[0]}/me/requests/{draft.id}/submit",
            headers=demo[2]["employee"],
            json={"revision": draft.revision},
        )
    db.expire_all()
    assert db.get(ProfileChangeRequest, draft.id).status == "draft"
    assert db.get(ChangeItem, draft.items[0].id).decision is None
    assert (
        list(
            db.exec(
                select(ProfileVersion.id).where(ProfileVersion.org_id == owner.org_id)
            ).all()
        )
        == versions
    )
    assert not db.exec(
        select(ReviewDecision).where(ReviewDecision.org_id == owner.org_id)
    ).all()


def test_approval_policy_migration(migration_engine: Engine) -> None:
    migrate(migration_engine, "downgrade", "b117002bc001")
    assert "approval_policy" not in inspect(migration_engine).get_table_names()
    migrate(migration_engine, "upgrade", "head")
    assert "approval_policy" in inspect(migration_engine).get_table_names()
    foreign_keys = inspect(migration_engine).get_foreign_keys("approval_policy")
    assert any(fk["referred_columns"] == ["org_id", "user_id"] for fk in foreign_keys)


def test_policy_concurrent_saves_have_distinct_versions(
    demo: tuple, db: Session
) -> None:
    admin = actor(db, demo[1]["E02"].org_id, "admin@demo.example.com")
    db.commit()

    def update(days: int) -> int:
        with Session(engine) as session:
            return policy.save_policy(
                session, admin, policy.PolicyInput(owner_review_days=days)
            ).current.version

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(update, [2, 3])) == [1, 2]
    assert len(policy.policy_view(db, admin.org_id).history) == 2


def test_auto_publish_rejects_stale_target_and_other_users(
    demo: tuple, db: Session, client: TestClient
) -> None:
    save(client, demo, require_manager=False)
    owner = actor(db, demo[1]["E02"].org_id, "employee.platform@demo.example.com")
    drafts = [
        employee.save_draft(
            owner,
            db,
            None,
            [
                ItemInput(
                    op="modify", capability_key="backend_api", statement="合成并发修改"
                )
            ],
            "web",
        )
        for _ in range(2)
    ]
    db.commit()
    for role in ("manager", "foreign"):
        assert (
            client.post(
                f"{demo[0]}/me/requests/{drafts[0].id}/submit",
                headers=demo[2][role],
                json={"revision": drafts[0].revision},
            ).status_code
            == 404
        )
    first = client.post(
        f"{demo[0]}/me/requests/{drafts[0].id}/submit",
        headers=demo[2]["employee"],
        json={"revision": drafts[0].revision},
    )
    assert first.status_code == 200 and first.json()["status"] == "approved"
    stale = client.post(
        f"{demo[0]}/me/requests/{drafts[1].id}/submit",
        headers=demo[2]["employee"],
        json={"revision": drafts[1].revision},
    )
    assert stale.status_code == 409
    db.expire_all()
    assert db.get(ProfileChangeRequest, drafts[1].id).status == "draft"


@pytest.mark.parametrize("operation", ["save", "preview"])
def test_owner_confirmation_keeps_policy_stable(
    demo: tuple, db: Session, operation: str
) -> None:
    org_id = demo[1]["E02"].org_id
    owner = actor(db, org_id, "employee.platform@demo.example.com")
    admin = actor(db, org_id, "admin@demo.example.com")
    items = [
        ItemInput(op="modify", capability_key="backend_api", statement="合成锁验证")
    ]
    draft = employee.save_draft(owner, db, None, items, "bot")
    db.commit()
    if operation == "save":
        employee.save_draft(owner, db, draft.id, items, "bot")
    else:
        employee.preview(owner, db, draft.id)
    with Session(engine) as concurrent:
        concurrent.execute(text("SET LOCAL lock_timeout = '100ms'"))
        with pytest.raises(OperationalError) as error:
            policy.save_policy(
                concurrent, admin, policy.PolicyInput(require_manager=False)
            )
        assert error.value.orig.sqlstate == "55P03"
    db.commit()
    assert (
        policy.save_policy(
            db, admin, policy.PolicyInput(require_manager=False)
        ).current.version
        == 1
    )
