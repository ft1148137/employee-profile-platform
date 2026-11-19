from datetime import timedelta
from uuid import uuid4

import pytest
from sqlmodel import Session, select

from app.domains.base import utc_now
from app.domains.capacity.models import (
    CapacityApproval,
    CapacityAssignment,
    CapacityDemand,
    CapacityDemandDependency,
    CapacityProject,
    CapacityProposal,
    CapacityUnavailability,
)
from app.domains.im.models import ImBinding, ImNotification
from app.domains.im.notifications import tracking_notice
from app.domains.inbox.models import InboxItem
from tests.api.routes.test_m6_capacity_assignment import case as case
from tests.domains.test_im_gateway import principal


@pytest.mark.parametrize("buffer_days", [0, 2])
def test_unavailability_notifies_managers_without_changing_commitments(
    case: tuple, db: Session, buffer_days: int
) -> None:
    api, create, demand, people, users, org = case
    create()
    (row,) = db.exec(
        select(CapacityAssignment).where(CapacityAssignment.org_id == org.id)
    ).all()
    project = CapacityProject(
        org_id=org.id,
        requesting_department_id=demand.requesting_department_id,
        requester_user_id=users["a"].id,
        title="合成项目",
        description="合成项目",
    )
    db.add(project)
    db.flush()
    demand.project_id = project.id
    demand.role_name = "开发"
    demand.committed_end_date = row.due_date
    demand.window_end = row.due_date

    start = row.due_date + timedelta(days=1 + buffer_days)
    downstream = CapacityDemand.model_validate(
        {
            **demand.model_dump(exclude={"id"}),
            "role_name": "验证",
            "window_start": start,
            "window_end": start + timedelta(days=4),
            "committed_start_date": start,
            "committed_end_date": start + timedelta(days=4),
        }
    )
    db.add(downstream)
    proposal = CapacityProposal(
        org_id=org.id,
        demand_id=demand.id,
        source_option_id=uuid4(),
        demand_revision=1,
        proposed_by_user_id=users["bm"].id,
        hold_expires_at=utc_now(),
    )
    db.add(proposal)
    db.flush()
    dependency = CapacityDemandDependency(
        org_id=org.id,
        project_id=project.id,
        demand_id=downstream.id,
        depends_on_demand_id=demand.id,
    )
    approval = CapacityApproval(
        org_id=org.id,
        proposal_id=proposal.id,
        user_id=users["bm"].id,
        party="resource_manager",
        decision="approved",
        via="web",
    )
    db.add_all(
        [
            dependency,
            approval,
            ImBinding(
                org_id=org.id,
                employee_id=people["bm"].id,
                platform="feishu",
                external_user_id="manager",
                external_chat_id="chat",
                bot_identity="app",
            ),
        ]
    )
    db.commit()
    subject = people["w"]
    commitments = (row, proposal, approval, demand, downstream)
    for item in commitments:
        db.refresh(item)
    before = [item.model_dump() for item in commitments]
    try:
        api(
            "/unavailability",
            "w",
            {
                "employee_id": str(subject.id),
                "start_date": (row.due_date - timedelta(days=1)).isoformat(),
                "end_date": (row.due_date + timedelta(days=1)).isoformat(),
            },
        )
        for item, expected in zip(commitments, before, strict=True):
            db.refresh(item)
            assert item.model_dump() == expected
        (notice, queued) = db.exec(
            select(InboxItem, ImNotification)
            .join(ImNotification, ImNotification.inbox_item_id == InboxItem.id)
            .where(InboxItem.org_id == org.id, ImNotification.org_id == org.id)
        ).one()
        assert notice.recipient_user_id == users["bm"].id
        assert queued.platform == "feishu"
        assert "受影响 2 天" in notice.message
        if buffer_days:
            assert "下游角色日期暂不受影响" in notice.message
            assert "下游角色：验证" not in notice.message
        else:
            assert "下游角色：验证" in notice.message
        assert f"项目预计比原窗口推迟 {2 - buffer_days} 天" in notice.message
        assert subject.display_name not in notice.message
        assert f"/capacity?tab=incoming&demand={demand.id}&plans=1" in notice.message
        manager = principal(db, org.id, people["bm"].id)
        assert "受影响 2 天" in (tracking_notice(db, notice, manager) or "")
        assert (
            tracking_notice(db, notice, principal(db, org.id, people["am"].id)) is None
        )
    finally:
        db.rollback()
        for absence in db.exec(
            select(CapacityUnavailability).where(
                CapacityUnavailability.org_id == org.id
            )
        ):
            db.delete(absence)
        db.delete(dependency)
        db.delete(approval)
        db.flush()
        db.delete(proposal)
        db.flush()
        db.delete(downstream)
        demand.project_id = None
        demand.role_name = None
        db.flush()
        db.delete(project)
        db.commit()
