from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import Engine, inspect, text
from sqlmodel import Session, select

from app.authz.principal import Principal
from app.core.security import create_access_token
from app.domains.change_request import employee
from app.domains.change_request.models import ChangeItemEvidence, ItemDecision
from app.domains.change_request.service import review_statuses
from app.domains.employee_tools import EMPLOYEE_TOOLS
from app.domains.evidence.models import Evidence
from app.domains.inbox.models import InboxItem
from app.domains.profile.models import ProfileClaim, ProfileVersion
from app.domains.review import appeal, service
from app.domains.review.appeal_schemas import ResolveAppealInput
from app.domains.review.models import ReviewAppeal
from app.models import User
from tests.api.routes.test_mcp import call, initialize, token_for
from tests.api.routes.test_reviews import actor, manager, pending, values
from tests.utils.migrations import migrate


@pytest.mark.parametrize("status", ["upheld", "reopened"])
def test_appeal_lifecycle(db: Session, demo: tuple, status: str) -> None:
    owner, request = pending(db, demo)
    reviewer = manager(db, demo)
    service.decide(
        reviewer,
        db,
        request.id,
        request.revision,
        values(request, ItemDecision.REJECTED),
    )
    db.commit()
    admin = actor(db, owner.org_id, "admin@demo.example.com")
    before_claims = [
        c.model_dump()
        for c in db.exec(
            select(ProfileClaim).where(ProfileClaim.org_id == owner.org_id)
        ).all()
    ]
    before_versions = [
        v.model_dump()
        for v in db.exec(
            select(ProfileVersion).where(ProfileVersion.org_id == owner.org_id)
        ).all()
    ]
    evidence_count = len(
        db.exec(select(Evidence).where(Evidence.org_id == owner.org_id)).all()
    )
    row = appeal.appeal_decision(
        owner, db, request.id, request.items[0].id, "合成申诉原因"
    )
    db.commit()
    assert [
        item.id
        for item in appeal.list_disputes(admin, db, employee_id=owner.employee_id)
    ] == [row.id]
    assert not appeal.list_disputes(admin, db, employee_id=uuid4())
    assert (
        next(
            i
            for i in employee.my_profile(owner, db).items
            if i.capability.key == "backend_api"
        ).status
        == "disputed"
    )
    assert (
        review_statuses(db, owner.org_id, [owner.employee_id], {owner.employee_id})[
            owner.employee_id
        ]
        == "disputed"
    )
    assert appeal.read_appeal(reviewer, db, row.id).reason == "合成申诉原因"
    for outsider in [
        Principal.system_worker(owner.org_id),
        replace(owner, employee_id=demo[1]["E03"].id, user_id=uuid4()),
        replace(owner, org_id=uuid4()),
    ]:
        with pytest.raises(HTTPException) as error:
            appeal.read_appeal(outsider, db, row.id)
        assert error.value.status_code == 404
    with pytest.raises(HTTPException) as duplicate:
        appeal.appeal_decision(owner, db, request.id, request.items[0].id, "再次申诉")
    assert duplicate.value.status_code == 409
    result = appeal.resolve_appeal(
        admin, db, row.id, ResolveAppealInput(status=status, reason="合成处理说明")
    )
    db.commit()
    assert appeal.read_appeal(owner, db, row.id).resolution_note == "合成处理说明"
    assert not appeal.list_disputes(admin, db)
    assert (
        next(
            i
            for i in employee.my_profile(owner, db).items
            if i.capability.key == "backend_api"
        ).status
        != "disputed"
    )
    assert (
        review_statuses(db, owner.org_id, [owner.employee_id], {owner.employee_id})[
            owner.employee_id
        ]
        != "disputed"
    )
    assert before_claims == [
        c.model_dump()
        for c in db.exec(
            select(ProfileClaim).where(ProfileClaim.org_id == owner.org_id)
        ).all()
    ]
    assert before_versions == [
        v.model_dump()
        for v in db.exec(
            select(ProfileVersion).where(ProfileVersion.org_id == owner.org_id)
        ).all()
    ]
    assert evidence_count == len(
        db.exec(select(Evidence).where(Evidence.org_id == owner.org_id)).all()
    )
    assert any(
        "申诉已处理" in n.message
        for n in db.exec(
            select(InboxItem).where(
                InboxItem.org_id == owner.org_id,
                InboxItem.recipient_employee_id == owner.employee_id,
            )
        ).all()
    )
    with pytest.raises(HTTPException) as resolved:
        appeal.resolve_appeal(
            admin, db, row.id, ResolveAppealInput(status="upheld", reason="重复处理")
        )
    assert resolved.value.status_code == 409
    if result.draft_request_id:
        draft = next(
            r
            for r in employee.my_requests(owner, db, "all").data
            if r.id == result.draft_request_id
        )
        assert draft.parent_request_id == request.id and draft.status == "draft"
        assert draft.items[0].decision is None
        old_refs = db.exec(
            select(ChangeItemEvidence.evidence_id).where(
                ChangeItemEvidence.org_id == owner.org_id,
                ChangeItemEvidence.item_id == request.items[0].id,
            )
        ).all()
        new_refs = db.exec(
            select(ChangeItemEvidence.evidence_id).where(
                ChangeItemEvidence.org_id == owner.org_id,
                ChangeItemEvidence.item_id == draft.items[0].id,
            )
        ).all()
        assert set(old_refs) == set(new_refs)
        assert (
            employee.submit(owner, db, draft.id, draft.revision, "web").status
            == "pending_review"
        )


def test_appeal_api_permissions_and_mcp(
    db: Session, demo: tuple, client: TestClient
) -> None:
    owner, request = pending(db, demo)
    base = f"/api/v1/orgs/{owner.org_id}/appeals"
    headers = demo[2]["employee"]
    before_passwords = {
        user.id: user.hashed_password for user in db.exec(select(User)).all()
    }
    args = {
        "request_id": str(request.id),
        "item_id": str(request.items[0].id),
        "reason": "合成申诉",
    }
    assert client.post(base, headers=headers, json=args).status_code == 409
    assert (
        client.post(base, headers=headers, json={**args, "reason": "  "}).status_code
        == 422
    )
    assert client.get(base, headers=headers).status_code == 403
    reviewer = manager(db, demo)
    service.decide(
        reviewer,
        db,
        request.id,
        request.revision,
        values(request, ItemDecision.REJECTED),
    )
    db.commit()
    outsider = actor(db, owner.org_id, "employee.algorithm@demo.example.com")
    other = {
        "Authorization": f"Bearer {create_access_token(outsider.user_id, expires_delta=timedelta(minutes=5))}"
    }
    assert client.post(base, headers=other, json=args).status_code == 404
    assert (
        client.post(
            base, headers=headers, json={**args, "item_id": str(uuid4())}
        ).status_code
        == 404
    )
    mcp_headers, _ = token_for(db, demo[1]["E02"])
    with TestClient(client.app) as running:
        result = call(
            running, initialize(running, mcp_headers), "appeal_decision", args
        )
        assert not result.get("isError"), result
    appeal_id = result["structuredContent"]["id"]
    assert client.get(f"{base}/{appeal_id}", headers=headers).status_code == 200
    assert client.get(f"{base}/{appeal_id}", headers=other).status_code == 404
    assert client.post(base, headers=headers, json=args).status_code == 409
    assert (
        client.post(
            f"{base}/{appeal_id}/resolve",
            headers=headers,
            json={"status": "upheld", "reason": "无权处理"},
        ).status_code
        == 403
    )
    # The recorded acting-for user can read even without an employee identity.
    decision = db.exec(
        select(service.ReviewDecision).where(
            service.ReviewDecision.org_id == owner.org_id,
            service.ReviewDecision.request_id == request.id,
        )
    ).one()
    admin = actor(db, owner.org_id, "admin@demo.example.com")
    decision.acting_for_user_id = admin.user_id
    db.commit()
    assert appeal.read_appeal(
        replace(admin, employee_id=None, is_org_admin=False), db, UUID(appeal_id)
    ).id
    assert not next(
        t for t in EMPLOYEE_TOOLS if t.name == "appeal_decision"
    ).bot_allowed
    assert before_passwords == {
        user.id: user.hashed_password for user in db.exec(select(User)).all()
    }


def test_existing_open_appeal_returns_conflict(db: Session, demo: tuple) -> None:
    owner, request = pending(db, demo)
    service.decide(
        manager(db, demo),
        db,
        request.id,
        request.revision,
        values(request, ItemDecision.REJECTED),
    )
    db.add(
        ReviewAppeal(
            org_id=owner.org_id,
            request_id=request.id,
            item_id=request.items[0].id,
            employee_id=owner.employee_id,
            reason="已存在的合成申诉",
        )
    )
    db.commit()
    with pytest.raises(HTTPException) as conflict:
        appeal.appeal_decision(
            owner, db, request.id, request.items[0].id, "重复的合成申诉"
        )
    assert conflict.value.status_code == 409


def test_appeal_migration_roundtrip(migration_engine: Engine) -> None:
    migrate(migration_engine, "downgrade", "b141001bc001")
    with migration_engine.connect() as connection:
        assert "review_appeal" not in inspect(connection).get_table_names()
    migrate(migration_engine, "upgrade", "b127001bc001")
    with migration_engine.connect() as connection:
        inspector = inspect(connection)
        assert {
            tuple(fk["constrained_columns"])
            for fk in inspector.get_foreign_keys("review_appeal")
        } >= {
            ("org_id", "item_id"),
            ("org_id", "request_id"),
            ("org_id", "employee_id"),
            ("org_id", "resolved_by_user_id"),
        }
        assert (
            connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            == "b127001bc001"
        )
        assert next(
            i
            for i in inspector.get_indexes("review_appeal")
            if i["name"] == "uq_open_appeal_item"
        )["unique"]
