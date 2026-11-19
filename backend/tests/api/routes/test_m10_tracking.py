from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta
from typing import NamedTuple
from uuid import UUID, uuid4

import httpx
import pytest
from sqlmodel import Session, delete, select

from app.connectors.jira.client import CloudJiraClient, DataCenterJiraClient
from app.core.db import engine
from app.domains.base import utc_now
from app.domains.capacity import tracking
from app.domains.capacity.models import (
    CapacityAssignment,
    CapacityAssignmentTask,
    CapacityProposal,
    CapacityProposalLine,
    CapacityUnavailability,
)
from app.domains.im.models import ImBinding, ImNotification, ImSetting
from app.domains.im.notifications import claim, tracking_notice
from app.domains.inbox.models import InboxItem, InboxKind
from app.domains.inbox.service import add_item, my_inbox
from app.domains.org.models import (
    Department,
    ExternalIdentity,
    Organization,
    OrgMembership,
)
from app.domains.source import sync
from app.domains.source.models import SourceConnection, SyncRun
from app.domains.source.schemas import JiraConfig
from app.domains.work.models import Task
from app.worker import tasks as worker_tasks
from app.worker.tasks import sync_run
from tests.api.routes.test_m6_capacity_assignment import case as case
from tests.api.routes.test_sources import BODY
from tests.domains.test_im_gateway import principal


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
    for state, summary in (
        (None, "尚未关联任务"),
        ("on_track", "执行正常"),
        ("tasks_done", "关联任务已全部完成，待承接部门确认结束"),
        ("status_unknown", "有关联任务状态待确认"),
    ):
        row.tracking_state = state
        db.commit()
        assert api(risk, "a") == {"message": f"该角色执行跟踪：{summary}"}
    row.due_date = row.start_date = date(2020, 1, 1)
    task.attributes = {"status_category": "indeterminate"}
    tracking.recompute(db, tracked.org)
    db.commit()
    assert api(risk, "a") == {"message": "该角色有延期风险"}
    assert api(risk, "am") == {"message": "该角色有延期风险"}
    assert people["w"].display_name not in str(api(risk, "a"))
    assert task.external_key not in str(api(risk, "a"))
    assert str(people["w"].id) not in str(api(risk, "a"))
    assert task.title not in str(api(risk, "a"))
    api(tracked.path, "bm", {"status": "done"}, method="PATCH")
    assert api(risk, "a") == {"message": "该角色执行跟踪：已完成"}


@pytest.mark.parametrize(
    ("source", "status", "category", "late", "owner", "expected"),
    [
        ("jira", "Done", None, True, None, "status_unknown"),
        ("jira", "Done", "other", True, None, "status_unknown"),
        ("jira", "客户已验收", "done", True, None, "tasks_done"),
        ("jira", "Done", "new", True, None, "overdue"),
        ("jira", "处理中", "indeterminate", False, "replacement", "handed_over"),
        ("csv:synthetic", "已完成", None, False, None, "tasks_done"),
        ("csv:synthetic", "进行中", None, True, None, "overdue"),
        ("csv:synthetic", "", None, True, None, "status_unknown"),
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
    if late and expected == "status_unknown":
        known = Task(
            org_id=row.org_id,
            source="jira",
            external_key="OPEN",
            title="合成待办",
            attributes={"status_category": "new"},
        )
        assert tracking.tracking_state(db, row, [task, known], now) == "overdue"


def test_borrowed_tasks_visible_to_owner_and_lender(
    case: tuple, db: Session, tracked: Tracked
) -> None:
    api, _, demand, people, users, _ = case
    active_borrows = "/assignments/active-borrows"
    feedback = "/assignments/feedback"
    tracking_path = tracked.path + "/tracking"
    assert api(active_borrows, "cm") == []
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
    db.add_all(
        [
            ImBinding(
                org_id=tracked.org,
                employee_id=people["cm"].id,
                platform="feishu",
                external_user_id="manager",
                external_chat_id="chat",
                bot_identity="app",
            ),
            ImSetting(
                org_id=tracked.org,
                platform="feishu",
                enabled=True,
                bot_identity="app",
                updated_by_user_id=users["admin"].id,
            ),
        ]
    )
    collision_line = CapacityProposalLine.model_validate(
        {**line.model_dump(exclude={"id"}), "employee_id": people["cm"].id}
    )
    db.add(collision_line)
    db.flush()
    collision = CapacityAssignment.model_validate(
        {
            **row.model_dump(),
            "id": UUID(hex=uuid4().hex[:24] + row.id.hex[-8:]),
            "employee_id": people["cm"].id,
            "proposal_line_id": collision_line.id,
        }
    )
    db.commit()
    try:
        tasks = tracked.path + "/tasks"
        visible = {
            "id": str(tracked.assignment),
            "employee_name": people["w"].display_name,
            "demand_title": demand.description,
            "start_date": row.start_date.isoformat(),
            "due_date": row.due_date.isoformat(),
        }
        assert api(active_borrows, "cm") == [visible]
        assert api(feedback, "cm") == []
        for role in ("a", "am", "bm", "w", "admin"):
            assert api(active_borrows, role) == []
        assert api(active_borrows, "foreign", other_org=True) == []
        api(active_borrows, "foreign", status=404)
        assert (
            api(tasks, "cm", {"task_id": str(tracked.task)})["tracking_state"]
            == "status_unknown"
        )
        assert api(tracking_path, "w")["tasks"][0]["external_key"] == "PRIVATE-123"
        api(tracking_path, "bm", status=404)
        api(tasks, "bm", {"task_id": str(tracked.task)}, status=404)
        listing = api(f"/demands/{demand.id}/assignments", "bm")["data"]
        assert (
            listing[0]["headcount"] == 1
            and listing[0]["tracking_state"] == "status_unknown"
        )
        assert {"id", "employee_id", "employee_name", "tasks"}.isdisjoint(listing[0])
        inbox = select(InboxItem).where(
            InboxItem.org_id == tracked.org,
            InboxItem.message.startswith(tracking.TRACKING_NOTICE),
        )
        assert db.exec(inbox).all() == []  # First evaluation is silent.
        api(tasks + f"/{tracked.task}", "cm", method="DELETE")
        assert [item.recipient_user_id for item in db.exec(inbox)] == [users["bm"].id]
        api(tasks, "w", {"task_id": str(tracked.task)})
        notices = list(db.exec(inbox))
        assert len(notices) == 3
        absence = CapacityUnavailability(
            org_id=tracked.org,
            employee_id=row.employee_id,
            start_date=row.due_date,
            end_date=row.due_date,
            created_by_user_id=users["w"].id,
        )
        tracking.notify_unavailability(db, absence)
        db.commit()
        notices += db.exec(
            select(InboxItem).where(
                InboxItem.org_id == tracked.org,
                InboxItem.message.startswith(tracking.UNAVAILABILITY_NOTICE),
            )
        ).all()
        assert len(notices) == 5
        assert sum(item.recipient_user_id == users["bm"].id for item in notices) == 3
        borrow_notices = [
            item for item in notices if item.recipient_user_id == users["cm"].id
        ]
        assert len(borrow_notices) == 2
        for item in notices:
            assert (
                f"分配 #{row.id.hex[-8:]}（{row.start_date} 至 {row.due_date}，{row.allocation_percent}%）"
                in item.message
            )
            assert people["w"].display_name not in item.message
            assert str(row.employee_id) not in item.message
        manager = principal(db, tracked.org, people["cm"].id)
        other_manager = principal(db, tracked.org, people["am"].id)
        public = my_inbox(manager, db).model_dump_json()
        assert "borrow_assignment_id" not in public and str(row.id) not in public
        db.add(collision)
        legacy = InboxItem(
            org_id=tracked.org,
            recipient_user_id=users["cm"].id,
            kind=InboxKind.CAPACITY,
            message=borrow_notices[0].message,
        )
        add_item(db, legacy)
        db.commit()
        assert {entry["id"] for entry in api(active_borrows, "cm")} == {
            str(row.id),
            str(collision.id),
        }
        pending = {
            notification.inbox_item_id: notification
            for notification in db.exec(
                select(ImNotification).where(
                    ImNotification.org_id == tracked.org,
                    ImNotification.employee_id == people["cm"].id,
                )
            )
        }
        for item in borrow_notices:
            queued = pending[item.id]
            assert queued.borrow_assignment_id == row.id
            assert item.message.endswith("/capacity?tab=borrows")
            assert (
                tracking_notice(db, item, other_manager, borrow_assignment_id=row.id)
                is None
            )
            with claim(engine, tracked.org, queued.id) as notice:
                assert notice is not None
                assert notice.text.endswith("/capacity?tab=borrows")
                assert str(row.id) not in notice.text
        with claim(engine, tracked.org, pending[legacy.id].id) as notice:
            assert notice is None

        foreign_org = db.exec(
            select(OrgMembership.org_id).where(
                OrgMembership.user_id == users["foreign"].id
            )
        ).one()
        with claim(engine, foreign_org, pending[borrow_notices[0].id].id) as notice:
            assert notice is None

        subject = people["w"]
        subject.department_id = demand.target_department_id
        db.commit()
        assert [entry["id"] for entry in api(active_borrows, "cm")] == [
            str(collision.id)
        ]
        for item in borrow_notices:
            with claim(engine, tracked.org, pending[item.id].id) as notice:
                assert notice is None
        for queued in pending.values():
            db.refresh(queued)
            assert queued.status == "failed" and queued.attempts == 0
        db.delete(collision)
        db.commit()
        assert api(active_borrows, "cm") == []
        api(tracking_path, "cm", status=404)
        assert api(active_borrows, "bm") == [visible]
        assert len(api(tracking_path, "bm")["tasks"]) == 1

        subject.department_id = lender.id
        subject.employment_status = "inactive"
        db.commit()
        assert api(active_borrows, "cm") == []
        api(tracking_path, "cm", status=404)

        subject.employment_status = "active"
        db.commit()
        api(tracked.path, "cm", {"status": "done"}, method="PATCH")
        assert api(active_borrows, "cm") == []
        assert [entry["id"] for entry in api(feedback, "cm")] == [visible["id"]]
        assert api(feedback, "bm") == []
    finally:
        db.rollback()
        if leftover := db.get(CapacityAssignment, collision.id):
            db.delete(leftover)
        db.refresh(row)
        row.proposal_line_id = None
        lender.parent_department_id = original_parent
        db.flush()
        db.delete(collision_line)
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
        == "status_unknown"
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
        assert run() == "status_unknown"
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


def test_first_recompute_is_silent_and_link_changes_notify(
    case: tuple, db: Session, tracked: Tracked
) -> None:
    api, _, _, people, _, _ = case
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
    row = db.get(CapacityAssignment, tracked.assignment)
    assert row and row.tracking_state is None and row.tracking_changed_at is None

    inbox = select(InboxItem).where(InboxItem.org_id == tracked.org)
    im = select(ImNotification.id).where(ImNotification.org_id == tracked.org)
    before = {item.id for item in db.exec(inbox)}
    im_before = set(db.exec(im))

    tracking.recompute(db, tracked.org)
    db.commit()
    assert row.tracking_state == "no_tasks" and row.tracking_changed_at is not None
    assert {item.id for item in db.exec(inbox)} == before
    assert set(db.exec(im)) == im_before

    tasks = tracked.path + "/tasks"
    assert (
        api(tasks, "w", {"task_id": str(tracked.task)})["tracking_state"]
        == "status_unknown"
    )
    linked = [item for item in db.exec(inbox) if item.id not in before]
    assert len(linked) == 1 and "有关联任务状态待确认" in linked[0].message
    assert len(set(db.exec(im)) - im_before) == 1

    result = api(tasks + f"/{tracked.task}", "w", method="DELETE")
    assert result["tracking_state"] == "no_tasks"
    unlinked = [
        item for item in db.exec(inbox) if item.id not in before | {linked[0].id}
    ]
    assert len(unlinked) == 1 and "尚未关联任务" in unlinked[0].message
    assert len(set(db.exec(im)) - im_before) == 2
    after = {item.id for item in db.exec(inbox)}
    api(tasks, "bm", {"task_id": str(tracked.task)})
    api(tasks + f"/{tracked.task}", "bm", method="DELETE")
    assert {item.id for item in db.exec(inbox)} == after


def test_jira_sync_keeps_import_when_tracking_refresh_fails(
    case: tuple,
    db: Session,
    tracked: Tracked,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _, _, _, people, users, _ = case
    config = JiraConfig.model_validate({**BODY, "start_date": "2000-01-01"})
    connection = SourceConnection(
        org_id=tracked.org,
        kind="jira",
        config=config.model_dump(mode="json"),
        updated_by_user_id=users["admin"].id,
    )
    db.add(connection)
    db.flush()
    run = SyncRun(
        org_id=tracked.org,
        connection_id=connection.id,
        requested_by_user_id=users["admin"].id,
        input={"project_keys": []},
        counts={"records": 0},
    )
    db.add(run)
    db.commit()
    run_id = run.id
    titles = {"JIRA-1": "合成导入任务甲", "JIRA-2": "合成导入任务乙"}
    name, email = people["w"].display_name, users["w"].email

    def jira_response(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "isLast": True,
                "issues": [
                    {
                        "key": key,
                        "fields": {
                            "summary": title,
                            "created": "2026-03-01T10:00:00Z",
                            "updated": "2026-03-02T10:00:00Z",
                        },
                    }
                    for key, title in titles.items()
                ],
            },
        )

    def fail_after_refresh(session: Session, org_id: UUID) -> None:
        tracking.recompute(session, org_id)
        session.flush()
        raise RuntimeError(f"{name} {email} {list(titles.values())}")

    monkeypatch.setattr(worker_tasks, "recompute", fail_after_refresh)
    with httpx.Client(transport=httpx.MockTransport(jira_response)) as http:
        monkeypatch.setattr(
            sync,
            "jira_client_for",
            lambda _: CloudJiraClient(config, "synthetic", http),
        )
        sync_run(str(tracked.org), str(run_id))

    db.expire_all()
    imported = db.exec(
        select(Task.external_key, Task.title).where(
            Task.org_id == tracked.org, Task.source == "jira"
        )
    ).all()
    assert dict(imported) == {**titles, "PRIVATE-123": "合成标题"}
    assert run.status == "succeeded" and run.error is None
    assert run.counts["records"] == 2
    assert connection.synced_until == datetime(2026, 3, 2, 10, tzinfo=UTC)
    row = db.get(CapacityAssignment, tracked.assignment)
    assert row and row.tracking_state is None and row.tracking_changed_at is None
    assert "Tracking refresh failed after sync" in caplog.text
    assert all(value not in caplog.text for value in (name, email, *titles.values()))


def test_daily_tracking_refresh_continues_after_one_org_fails(
    case: tuple,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _, _, _, _, users, org = case
    foreign_org_id = db.exec(
        select(OrgMembership.org_id).where(OrgMembership.user_id == users["foreign"].id)
    ).one()
    org_ids = {org.id, foreign_org_id}
    attempted: list[UUID] = []
    marker = "合成重算标记"

    def refresh(session: Session, org_id: UUID) -> None:
        if org_id not in org_ids:
            return
        attempted.append(org_id)
        organization = session.get(Organization, org_id)
        assert organization
        organization.name = marker
        session.flush()
        if len(attempted) == 1:
            raise RuntimeError(marker)

    monkeypatch.setattr(worker_tasks, "recompute", refresh)
    worker_tasks.refresh_tracking(0)

    assert len(attempted) == 2 and set(attempted) == org_ids
    db.expire_all()
    first, second = [db.get(Organization, org_id) for org_id in attempted]
    assert first and first.name != marker
    assert second and second.name == marker
    assert f"Daily tracking refresh failed for org {first.id}" in caplog.text
    assert marker not in caplog.text
