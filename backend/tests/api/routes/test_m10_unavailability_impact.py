from datetime import timedelta
from uuid import uuid4

import pytest
from sqlmodel import Session, col, delete, select

from app.core.config import settings
from app.domains.base import utc_now
from app.domains.capacity.models import (
    CapacityApproval,
    CapacityAssignment,
    CapacityDemand,
    CapacityDemandDependency,
    CapacityDemandEvent,
    CapacityProject,
    CapacityProposal,
    CapacityUnavailability,
)
from app.domains.capacity.tracking import UNAVAILABILITY_NOTICE
from app.domains.im.models import ImBinding, ImNotification
from app.domains.im.notifications import tracking_notice
from app.domains.inbox.models import InboxItem
from tests.api.routes.test_m6_capacity_assignment import case as case
from tests.api.routes.test_m7_commitment_load_words import seed_dictionary
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
        assert tracking_notice(db, notice, manager) == (
            f"{UNAVAILABILITY_NOTICE}有更新，请前往网页查看。\n"
            f"{settings.FRONTEND_HOST.rstrip('/')}/capacity?tab=incoming&demand={demand.id}&plans=1"
        )
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


def test_unavailability_respects_pending_project_forecast_until_new_reply(
    case: tuple, db: Session
) -> None:
    api, create, assigned_role, people, users, org = case
    seed_dictionary(db, org.id)
    create()
    assignment = db.exec(
        select(CapacityAssignment).where(CapacityAssignment.org_id == org.id)
    ).one()
    project = CapacityProject(
        org_id=org.id,
        requesting_department_id=assigned_role.requesting_department_id,
        requester_user_id=users["a"].id,
        title="合成项目",
        description="合成项目",
    )
    db.add(project)
    db.flush()
    assigned_role.project_id = project.id
    assigned_role.role_name = "开发"
    assigned_role.committed_end_date = assigned_role.window_end = assignment.due_date

    def role(name: str, offset: int, length: int) -> CapacityDemand:
        start = assignment.due_date + timedelta(days=offset)
        return CapacityDemand.model_validate(
            {
                **assigned_role.model_dump(exclude={"id"}),
                "role_name": name,
                "status": "submitted",
                "window_start": start,
                "window_end": start + timedelta(days=length - 1),
                "committed_start_date": None,
                "committed_end_date": None,
            }
        )

    blocker = role("前置验收", -2, 2)
    known = role("开发验证", 1, 3)
    unknown = role("交付验证", 1, 3)
    roles = [blocker, known, unknown]
    db.add_all(roles)
    db.flush()
    db.add_all(
        CapacityDemandDependency(
            org_id=org.id,
            project_id=project.id,
            demand_id=dependent,
            depends_on_demand_id=parent,
        )
        for dependent, parent in [
            (known.id, assigned_role.id),
            (unknown.id, assigned_role.id),
            (unknown.id, blocker.id),
        ]
    )
    db.commit()

    def notify_for_day(offset: int) -> str:
        day = (assignment.due_date + timedelta(days=offset)).isoformat()
        api(
            "/unavailability",
            "w",
            {"employee_id": str(people["w"].id), "start_date": day, "end_date": day},
        )
        return db.exec(
            select(InboxItem.message)
            .where(
                InboxItem.org_id == org.id,
                InboxItem.recipient_user_id == users["bm"].id,
                col(InboxItem.message).startswith(UNAVAILABILITY_NOTICE),
            )
            .order_by(col(InboxItem.created_at).desc(), col(InboxItem.id).desc())
            .limit(1)
        ).one()

    try:
        blocker_url = f"/demands/{blocker.id}"
        project_url = f"/projects/{project.id}"
        reply = api(
            f"{blocker_url}/reply",
            "bm",
            {
                "outcome": "not_now",
                "reason_code": "scope",
                "text": "暂时无法承接",
                "revision": blocker.revision,
            },
        )
        follow_up = api(
            f"{blocker_url}/respond",
            "a",
            {
                "action": "follow_up",
                "text": "请重新确认日期",
                "revision": reply["revision"],
            },
        )
        assert api(project_url, "a")["schedule"]["forecast_unconfirmed"]
        pending = notify_for_day(-2)
        assert "受影响 1 天" in pending
        assert "受影响的下游角色：开发验证" in pending
        assert "日期待确认的下游角色：交付验证" in pending
        assert "项目预计完工日待确认" in pending
        assert "项目预计比原窗口推迟" not in pending

        api(
            f"{blocker_url}/reply",
            "bm",
            {
                "outcome": "can_do",
                "text": "可以承接",
                "revision": follow_up["revision"],
                "start_date": blocker.window_start.isoformat(),
                "end_date": blocker.window_end.isoformat(),
            },
        )
        schedule = api(project_url, "a")["schedule"]
        assert not schedule["forecast_unconfirmed"] and schedule["delay_days"] == 0
        restored = notify_for_day(0)
        assert (
            "受影响的下游角色：开发验证（承接部门）、交付验证（承接部门）" in restored
        )
        assert "项目预计比原窗口推迟 1 天" in restored
        assert "待确认" not in restored
    finally:
        db.rollback()
        db.exec(
            delete(CapacityUnavailability).where(
                CapacityUnavailability.org_id == org.id
            )
        )
        db.exec(
            delete(CapacityDemandDependency).where(
                CapacityDemandDependency.org_id == org.id,
                CapacityDemandDependency.project_id == project.id,
            )
        )
        db.exec(
            delete(CapacityDemandEvent).where(
                CapacityDemandEvent.org_id == org.id,
                CapacityDemandEvent.demand_id == blocker.id,
            )
        )
        for item in roles:
            db.delete(item)
        assigned_role.project_id = None
        db.flush()
        db.delete(project)
        db.commit()
