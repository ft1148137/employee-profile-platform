from collections.abc import Generator
from datetime import date, timedelta
from typing import NamedTuple
from uuid import UUID, uuid4

import httpx
import pytest
from sqlmodel import Session, delete, select

from app.connectors.jira.client import CloudJiraClient, DataCenterJiraClient
from app.domains.base import utc_now
from app.domains.capacity import tracking
from app.domains.capacity.models import (
    CapacityAssignment,
    CapacityAssignmentTask,
    CapacityProposal,
    CapacityProposalLine,
)
from app.domains.im.models import ImBinding, ImNotification
from app.domains.inbox.models import InboxItem
from app.domains.org.models import Department, ExternalIdentity, OrgMembership
from app.domains.source import sync
from app.domains.source.models import SourceConnection, SyncRun
from app.domains.source.schemas import JiraConfig
from app.domains.work.models import Task
from app.worker.tasks import sync_run
from tests.api.routes.test_m6_capacity_assignment import case as case
from tests.api.routes.test_sources import BODY


class Tracked(NamedTuple):
    org: UUID
    assignment: UUID
    path: str
    task: UUID


@pytest.fixture
def tracked(case: tuple, db: Session) -> Generator[Tracked]:
    _, create, _, _, _, org = case
    assigned = create()
    task = Task(
        org_id=org.id,
        source="jira",
        external_key="PRIVATE-123",
        title="合成标题",
        status="Done",
        attributes={"resolved": "2026-03-01T10:00:00Z"},
    )
    db.add(task)
    db.commit()
    org_id = org.id
    row = UUID(assigned["id"])
    yield Tracked(org_id, row, f"/assignments/{row}", task.id)
    db.rollback()
    db.exec(
        delete(CapacityAssignmentTask).where(CapacityAssignmentTask.org_id == org_id)
    )
    db.commit()


def test_permissions_and_requester_projection(
    case: tuple, db: Session, tracked: Tracked
) -> None:
    api, _, demand, people, users, _ = case
    risk = f"/demands/{demand.id}/tracking"
    tasks = tracked.path + "/tasks"
    body = {"task_id": str(tracked.task)}
    for role in ("a", "am", "w2", "admin"):
        api(tasks, role, body, status=404)
        api(tracked.path + "/tracking", role, status=404)
        api(tasks + "/search?q=PRIVATE", role, status=404)
    api(tasks + "/search?q=PRIVATE", "foreign", status=404, other_org=True)
    for role in ("w", "bm"):
        assert len(api(tasks + "/search?q=123", role)) == 1
        assert len(api(tasks + "/search?q=标题", role)) == 1
        assert api(tasks, role, body)["tasks"][0]["id"] == str(tracked.task)
        api(tasks + f"/{tracked.task}", role, method="DELETE")
    foreign_org = db.exec(
        select(OrgMembership.org_id).where(OrgMembership.user_id == users["foreign"].id)
    ).one()
    foreign_task = Task(
        org_id=foreign_org, source="jira", external_key="FOREIGN", title="合成外部任务"
    )
    db.add(foreign_task)
    db.commit()
    assert api(tasks + "/search?q=FOREIGN", "w") == []
    api(tasks, "bm", {"task_id": str(foreign_task.id)}, status=404)
    api(tasks, "w", body)
    row = db.get(CapacityAssignment, tracked.assignment)
    task = db.get(Task, tracked.task)
    assert row and task
    row.due_date = row.start_date = date(2020, 1, 1)
    task.attributes = {"status_category": "indeterminate"}
    tracking.recompute(db, tracked.org)
    db.commit()
    assert api(risk, "a") == {"message": "该角色有延期风险"}
    assert api(risk, "am") == {"message": "该角色有延期风险"}
    assert people["w"].display_name not in str(api(risk, "a"))
    assert task.external_key not in str(api(risk, "a"))


@pytest.mark.parametrize(
    ("source", "status", "category", "late", "owner", "expected"),
    [
        ("jira", "Done", None, True, None, "on_track"),
        ("jira", "Done", "other", True, None, "on_track"),
        ("jira", "客户已验收", "done", True, None, "tasks_done"),
        ("jira", "Done", "new", True, None, "overdue"),
        ("jira", "处理中", "indeterminate", False, "replacement", "handed_over"),
        ("csv:synthetic", "已完成", None, False, None, "tasks_done"),
        ("csv:synthetic", "进行中", None, True, None, "overdue"),
        ("csv:synthetic", "", None, True, None, "on_track"),
    ],
)
def test_tracking_status(
    case: tuple,
    db: Session,
    tracked: Tracked,
    source: str,
    status: str,
    category: str | None,
    late: bool,
    owner: str | None,
    expected: str,
) -> None:
    api, _, _, people, _, _ = case
    task = db.get(Task, tracked.task)
    row = db.get(CapacityAssignment, tracked.assignment)
    assert task and row
    task.source = source
    task.status = status
    task.attributes = {
        "resolved": "2026-03-01T10:00:00Z",
        "status_category": category,
        "people": [{"id": owner, "role": "assignee"}] if owner else [],
    }
    now = tracking.today(db, row.org_id)
    row.start_date = row.due_date = now - timedelta(days=1) if late else now
    if owner:
        db.add(
            ExternalIdentity(
                org_id=row.org_id,
                source=source,
                external_id=owner,
                employee_id=people["w2"].id,
            )
        )
    db.commit()
    assert api(tracked.path + "/tracking", "w")["tracking_state"] == "no_tasks"
    result = api(tracked.path + "/tasks", "w", {"task_id": str(tracked.task)})
    assert result["tracking_state"] == expected
    assert result["tracking_changed_at"]


def test_borrowed_tasks_visible_to_owner_and_lender(
    case: tuple, db: Session, tracked: Tracked
) -> None:
    api, _, demand, people, users, _ = case
    row = db.get(CapacityAssignment, tracked.assignment)
    assert row
    # The employee's manager C is now outside the target department's hierarchy.
    lender = db.get(Department, people["w"].department_id)
    assert lender
    original_parent = lender.parent_department_id
    lender.parent_department_id = None
    proposal = CapacityProposal(
        org_id=row.org_id,
        demand_id=row.demand_id,
        source_option_id=uuid4(),
        demand_revision=1,
        proposed_by_user_id=users["bm"].id,
        hold_expires_at=utc_now(),
    )
    db.add(proposal)
    db.flush()
    assert row.start_date is not None
    line = CapacityProposalLine(
        org_id=row.org_id,
        proposal_id=proposal.id,
        employee_id=row.employee_id,
        lender_department_id=lender.id,
        start_date=row.start_date,
        end_date=row.due_date,
        allocation_percent=row.allocation_percent,
    )
    db.add(line)
    db.flush()
    row.proposal_line_id = line.id
    db.commit()
    try:
        tasks = tracked.path + "/tasks"
        assert (
            api(tasks, "cm", {"task_id": str(tracked.task)})["tracking_state"]
            == "on_track"
        )
        assert (
            api(tracked.path + "/tracking", "w")["tasks"][0]["external_key"]
            == "PRIVATE-123"
        )
        api(tracked.path + "/tracking", "bm", status=404)
        api(tasks, "bm", {"task_id": str(tracked.task)}, status=404)
        listing = api(f"/demands/{demand.id}/assignments", "bm")["data"]
        assert (
            listing[0]["headcount"] == 1 and listing[0]["tracking_state"] == "on_track"
        )
        assert {"id", "employee_id", "employee_name", "tasks"}.isdisjoint(listing[0])
    finally:
        db.refresh(row)
        row.proposal_line_id = None
        lender.parent_department_id = original_parent
        db.flush()
        db.delete(line)
        db.flush()
        db.delete(proposal)
        db.commit()


@pytest.mark.parametrize("deployment", ["cloud", "data_center"])
def test_jira_sync_upgrades_legacy_state_and_notifies_once(
    case: tuple,
    db: Session,
    tracked: Tracked,
    monkeypatch: pytest.MonkeyPatch,
    deployment: str,
) -> None:
    api, _, _, people, users, _ = case
    current_status = {"name": "客户已验收"}
    config = JiraConfig.model_validate(
        {
            **BODY,
            "start_date": "2000-01-01",
            "deployment": deployment,
            "auth_type": "api_token" if deployment == "cloud" else "pat",
        }
    )
    adapter = CloudJiraClient if deployment == "cloud" else DataCenterJiraClient
    connection = SourceConnection(
        org_id=tracked.org,
        kind="jira",
        config=config.model_dump(mode="json"),
        updated_by_user_id=users["admin"].id,
    )
    db.add(connection)
    db.add(
        ImBinding(
            org_id=tracked.org,
            employee_id=people["bm"].id,
            platform="feishu",
            external_user_id="synthetic-manager",
            external_chat_id="chat",
            bot_identity="app",
        )
    )
    db.commit()
    assert (
        api(tracked.path + "/tasks", "w", {"task_id": str(tracked.task)})[
            "tracking_state"
        ]
        == "on_track"
    )

    def jira_response(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "isLast": True,
                "total": 1,
                "issues": [
                    {
                        "key": "PRIVATE-123",
                        "fields": {
                            "summary": "合成标题",
                            "status": current_status,
                            "created": "2026-03-01T10:00:00Z",
                            "updated": "2026-03-02T10:00:00Z",
                        },
                    }
                ],
            },
        )

    def run() -> str:
        queued = SyncRun(
            org_id=tracked.org,
            connection_id=connection.id,
            requested_by_user_id=users["admin"].id,
            input={"project_keys": []},
            counts={"records": 0},
        )
        db.add(queued)
        db.flush()
        run_id = queued.id
        db.commit()
        sync_run(str(tracked.org), str(run_id))
        db.expire_all()
        assert (
            db.exec(
                select(SyncRun.status).where(
                    SyncRun.org_id == tracked.org, SyncRun.id == run_id
                )
            ).one()
            == "succeeded"
        )
        return api(tracked.path + "/tracking", "w")["tracking_state"]

    with httpx.Client(transport=httpx.MockTransport(jira_response)) as http:
        monkeypatch.setattr(
            sync,
            "jira_client_for",
            lambda _: adapter(config, "synthetic", http),
        )
        assert run() == "on_track"
        current_status["statusCategory"] = {"key": "done"}
        assert run() == "tasks_done"
        first = api(tracked.path + "/tracking", "w")
        assert run() == "tasks_done"
        assert api(tracked.path + "/tracking", "w") == first
    task = db.get(Task, tracked.task)
    assert (
        task
        and task.status == "客户已验收"
        and task.attributes["status_category"] == "done"
    )
    notices = db.exec(
        select(InboxItem, ImNotification)
        .join(ImNotification, ImNotification.inbox_item_id == InboxItem.id)
        .where(
            InboxItem.org_id == tracked.org,
            ImNotification.org_id == tracked.org,
            InboxItem.recipient_user_id == users["bm"].id,
        )
    ).all()
    assert sum("关联任务已全部完成" in item.message for item, _ in notices) == 1
