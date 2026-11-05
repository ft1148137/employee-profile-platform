from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import Engine, inspect
from sqlmodel import Session, select

from app.authz.principal import Principal
from app.domains import manager_tools
from app.domains.audit.models import AuditEvent
from app.domains.base import utc_now
from app.domains.change_request.models import ReviewDecision
from app.domains.evidence.service import read_evidence
from app.domains.inbox.models import InboxItem, InboxKind
from app.domains.org.models import OrgMembership
from app.domains.review import delegation, service
from app.domains.review.models import ReviewDelegation
from app.domains.review.routing import can_review
from app.models import User
from tests.api.routes.test_reviews import actor, manager, pending, values
from tests.utils.migrations import migrate


def setup_delegation(
    db: Session, demo: tuple, *, delegate: Principal | None = None, **times: datetime
) -> ReviewDelegation:
    principal = manager(db, demo)
    delegate = delegate or actor(db, principal.org_id, "admin@demo.example.com")
    now = utc_now()
    delegation.create_delegation(
        db,
        principal,
        delegation.DelegationInput(
            delegate_user_id=delegate.user_id,
            starts_at=times.get("starts_at", now - timedelta(minutes=1)),
            ends_at=times.get("ends_at", now + timedelta(days=1)),
        ),
    )
    db.commit()
    return db.exec(
        select(ReviewDelegation).where(ReviewDelegation.org_id == principal.org_id)
    ).one()


def test_delegate_reads_decides_and_notifies_without_employee(
    demo: tuple, db: Session
) -> None:
    row = setup_delegation(db, demo)
    owner, request = pending(db, demo)
    delegate = actor(db, owner.org_id, "admin@demo.example.com")
    assert delegate.employee_id is None
    assert can_review(db, manager(db, demo), owner.employee_id) == row.manager_user_id
    assert request.id in {r.id for r in service.list_pending_reviews(delegate, db).data}
    detail = manager_tools.get_review(delegate, db, request.id)
    assert detail.acting_for_name
    evidence_id = detail.items[0].evidence[0].id
    assert read_evidence(db, delegate, evidence_id).content is not None
    recipients = set(
        db.exec(
            select(InboxItem.recipient_user_id).where(
                InboxItem.org_id == owner.org_id,
                InboxItem.request_id == request.id,
                InboxItem.kind == InboxKind.REVIEW_REQUESTED,
            )
        ).all()
    )
    assert recipients == {row.manager_user_id, row.delegate_user_id}
    result = manager_tools.decide_review_items(
        delegate, db, request.id, request.revision, values(request)
    )
    db.commit()
    decision = db.get(ReviewDecision, result.decision_id)
    assert decision.acting_for_user_id == row.manager_user_id
    assert decision.reviewer_user_id == row.delegate_user_id
    audit = db.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == owner.org_id,
            AuditEvent.action == "change_request.decided",
        )
    ).one()
    assert audit.details["acting_for_user_id"] == str(row.manager_user_id)
    assert read_evidence(db, delegate, evidence_id).content is None


@pytest.mark.parametrize(
    "state", ["future", "expired", "revoked", "disabled", "chain", "self"]
)
def test_delegation_permission_boundaries(demo: tuple, db: Session, state: str) -> None:
    manager_actor = manager(db, demo)
    delegate = actor(db, manager_actor.org_id, "admin@demo.example.com")
    owner, request = pending(db, demo)
    if state == "self":
        delegate = owner
    row = setup_delegation(db, demo, delegate=delegate)
    if state == "future":
        row.starts_at = utc_now() + timedelta(hours=1)
    elif state == "expired":
        row.ends_at = utc_now()
    elif state == "revoked":
        delegation.revoke_delegation(db, manager_actor, row.id)
    elif state == "disabled":
        db.get(User, delegate.user_id).is_active = False
    elif state == "chain":
        chained = actor(db, owner.org_id, "employee.algorithm@demo.example.com")
        db.add(
            ReviewDelegation(
                org_id=owner.org_id,
                manager_user_id=delegate.user_id,
                delegate_user_id=chained.user_id,
                created_by_user_id=delegate.user_id,
                starts_at=row.starts_at,
                ends_at=row.ends_at,
            )
        )
        delegate = chained
    db.commit()
    assert can_review(db, delegate, owner.employee_id) is None
    assert request.id not in {
        r.id for r in service.list_pending_reviews(delegate, db).data if r.can_decide
    }
    for call in (
        lambda: service.get_review(delegate, db, request.id),
        lambda: service.decide(
            delegate, db, request.id, request.revision, values(request)
        ),
    ):
        with pytest.raises(HTTPException) as error:
            call()
        assert error.value.status_code == 403
    db.rollback()
    if state == "disabled":
        db.get(User, row.delegate_user_id).is_active = True
        db.commit()


def test_delegation_http_validation_and_ownership(
    demo: tuple, db: Session, client: TestClient
) -> None:
    url, _, auth = demo
    path = f"{url}/me/review-delegations"
    now = utc_now()
    body = {
        "delegate_user_id": str(
            actor(db, demo[1]["E01"].org_id, "admin@demo.example.com").user_id
        ),
        "starts_at": now.isoformat(),
        "ends_at": (now + timedelta(days=1)).isoformat(),
    }
    assert client.post(path, headers=auth["employee"], json=body).status_code == 403
    assert client.post(path, headers=auth["admin"], json=body).status_code == 403
    assert client.get(path, headers=auth["foreign"]).status_code == 404
    foreign_user_id = client.get("/api/v1/users/me", headers=auth["foreign"]).json()[
        "id"
    ]
    for candidate in [manager(db, demo).user_id, uuid4(), foreign_user_id]:
        assert (
            client.post(
                path,
                headers=auth["manager"],
                json={**body, "delegate_user_id": str(candidate)},
            ).status_code
            == 422
        )
    response = client.post(path, headers=auth["manager"], json=body)
    assert response.status_code == 201, response.text
    row_id = response.json()["data"][0]["id"]
    assert client.post(path, headers=auth["manager"], json=body).status_code == 409
    assert client.get(path, headers=auth["manager"]).json()["data"][0]["id"] == row_id
    assert client.delete(f"{path}/{row_id}", headers=auth["admin"]).status_code == 404
    assert client.delete(f"{path}/{row_id}", headers=auth["foreign"]).status_code == 404
    assert client.delete(f"{path}/{row_id}", headers=auth["manager"]).status_code == 204
    assert client.post(path, headers=auth["manager"], json=body).status_code == 201


def test_cross_tenant_delegate_has_no_review_access(
    demo: tuple, db: Session, client: TestClient
) -> None:
    row = setup_delegation(db, demo)
    owner, request = pending(db, demo)
    foreign_user_id = client.get("/api/v1/users/me", headers=demo[2]["foreign"]).json()[
        "id"
    ]
    foreign_org_id = db.exec(
        select(OrgMembership.org_id).where(
            OrgMembership.user_id == UUID(foreign_user_id)
        )
    ).one()
    assert (
        client.get(
            f"/api/v1/orgs/{foreign_org_id}/reviews/{request.id}",
            headers=demo[2]["foreign"],
        ).status_code
        == 404
    )
    principal = actor(db, row.org_id, "admin@demo.example.com")
    principal = type(principal)(
        principal.actor_type,
        foreign_org_id,
        principal.user_id,
        None,
        False,
        frozenset(),
    )
    assert can_review(db, principal, owner.employee_id) is None


def test_delegation_migration_roundtrip(migration_engine: Engine) -> None:
    migrate(migration_engine, "downgrade", "b127001bc001")
    assert "review_delegation" not in inspect(migration_engine).get_table_names()
    migrate(migration_engine, "upgrade", "head")
    fks = inspect(migration_engine).get_foreign_keys("review_delegation")
    assert (
        sum(
            fk["referred_table"] == "org_membership"
            and fk["referred_columns"] == ["org_id", "user_id"]
            for fk in fks
        )
        == 3
    )
    migrate(migration_engine, "downgrade", "b127001bc001")
    migrate(migration_engine, "upgrade", "head")
