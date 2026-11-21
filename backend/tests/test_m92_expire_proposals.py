"""Periodic proposal expiration isolates org errors and notifies on expiry."""

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete, select

from app.authz.principal import Principal
from app.domains.base import utc_now
from app.domains.capacity.models import (
    CapacityDemandEvent,
    DemandEventKind,
    DemandStatus,
    ProposalStatus,
)
from app.domains.inbox.models import InboxItem, InboxKind
from app.domains.org.models import Organization, OrgMembership
from app.worker import tasks
from tests.api.routes.test_m9_proposal import case as case
from tests.api.routes.test_m9_proposal import planning_setup as planning_setup
from tests.api.routes.test_m9_proposal import proposal_row, propose


def test_expire_proposals_isolates_org_errors(
    case: tuple,
    client: TestClient,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _, _, _, demand = case
    reply = propose(client, case)
    assert reply.status_code == 200, reply.text
    row = proposal_row(db, demand)
    row.hold_expires_at = utc_now() - timedelta(seconds=1)
    db.add(row)

    failing_org = Organization(
        id=UUID(int=max(1, demand.org_id.int - 1)),
        name="合成异常隔离企业",
        slug=f"failing-{uuid4().hex[:8]}",
    )
    assert failing_org.id < demand.org_id
    db.add(failing_org)
    db.commit()

    real_expire = tasks.expire
    seen_orgs: list[UUID] = []

    def fake_expire(principal: Principal, session: Session) -> int:
        seen_orgs.append(principal.org_id)
        if principal.org_id == failing_org.id:
            raise RuntimeError("sensitive org payload")
        return real_expire(principal, session)

    monkeypatch.setattr(tasks, "expire", fake_expire)

    try:
        with caplog.at_level("ERROR", logger=tasks.__name__):
            tasks.expire_proposals(0)
        assert seen_orgs.index(failing_org.id) < seen_orgs.index(demand.org_id)
        assert any(
            str(failing_org.id) in record.message and "RuntimeError" in record.message
            for record in caplog.records
        )
        assert "sensitive org payload" not in caplog.text
        expired = proposal_row(db, demand)
        assert expired.status == ProposalStatus.FAILED
        assert expired.fail_reason == "expired"
        db.refresh(demand)
        assert demand.status == DemandStatus.SUBMITTED
    finally:
        db.rollback()
        db.exec(delete(Organization).where(Organization.id == failing_org.id))
        db.commit()


def test_expire_proposals_marks_expired_and_notifies(
    case: tuple, client: TestClient, db: Session
) -> None:
    _, people, _, demand = case
    reply = propose(client, case)
    assert reply.status_code == 200, reply.text
    row = proposal_row(db, demand)
    row.hold_expires_at = utc_now() - timedelta(seconds=1)
    db.add(row)
    db.commit()

    existing_inbox_ids = set(
        db.exec(select(InboxItem.id).where(InboxItem.org_id == demand.org_id)).all()
    )
    b_user_id = db.exec(
        select(OrgMembership.user_id).where(
            OrgMembership.org_id == demand.org_id,
            OrgMembership.employee_id == people["E05"].id,
        )
    ).one()

    tasks.expire_proposals(0)

    db.expire_all()
    expired = proposal_row(db, demand)
    assert expired.status == ProposalStatus.FAILED
    assert expired.fail_reason == "expired"
    db.refresh(demand)
    assert demand.status == DemandStatus.SUBMITTED

    events = db.exec(
        select(CapacityDemandEvent).where(
            CapacityDemandEvent.org_id == demand.org_id,
            CapacityDemandEvent.demand_id == demand.id,
            CapacityDemandEvent.kind == DemandEventKind.PROPOSAL_FAILED,
        )
    ).all()
    assert len(events) == 1
    assert events[0].text == "方案已过期，需要重新规划"

    new_items = [
        item
        for item in db.exec(
            select(InboxItem).where(InboxItem.org_id == demand.org_id)
        ).all()
        if item.id not in existing_inbox_ids
    ]
    assert {item.recipient_user_id for item in new_items} >= {
        demand.requester_user_id,
        b_user_id,
    }
    assert all(
        item.kind == InboxKind.CAPACITY
        and item.message.startswith(
            f"方案已过期，需要重新规划（{demand.capability_name}，"
            f"{demand.window_start} 至 {demand.window_end}）。"
        )
        and "查看" in item.message
        for item in new_items
    )
