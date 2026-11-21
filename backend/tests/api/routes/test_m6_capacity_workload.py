from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlmodel import Session, col, select

from app.core.security import create_access_token
from app.domains.audit.models import AuditEvent
from app.domains.base import utc_now
from app.domains.capacity.calendar import week_start
from app.domains.capacity.models import (
    CapacityAssignment,
    CapacityDemand,
    CapacityDemandEvent,
    CapacityExtensionRequest,
    CapacitySetting,
    CapacityWeek,
    CapacityWorkloadRequest,
)
from app.domains.capacity.tools import CAPACITY_TOOLS
from app.domains.capacity.workload import WORKLOAD_REVIEW_NOTICE
from app.domains.im.models import ImBinding, ImNotification
from app.domains.inbox.models import InboxItem, InboxKind
from app.domains.org.models import Employee, ExternalIdentity, OrgMembership, RoleGrant
from app.domains.review.models import ReviewDelegation
from app.domains.source.models import SourceConnection
from app.domains.work.models import Task


@pytest.fixture
def capacity(demo: tuple, db: Session) -> Generator[tuple]:
    yield demo
    db.rollback()
    for model in (
        CapacityExtensionRequest,
        CapacityAssignment,
        CapacityDemandEvent,
        CapacityDemand,
        CapacityWorkloadRequest,
        CapacityWeek,
        CapacitySetting,
    ):
        db.execute(delete(model).where(model.org_id == demo[1]["E01"].org_id))
    db.commit()


def user_id(db: Session, e: Employee) -> UUID:
    return db.exec(
        select(OrgMembership.user_id).where(
            OrgMembership.org_id == e.org_id, OrgMembership.employee_id == e.id
        )
    ).one()


def headers(db: Session, e: Employee) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {create_access_token(user_id(db, e), expires_delta=timedelta(minutes=10))}"
    }


def week_body(employee_id: UUID, percent: object) -> dict:
    start = week_start(
        utc_now().astimezone(ZoneInfo("Asia/Shanghai")).date()
    ).isoformat()
    return {
        "items": [{"employee_id": str(employee_id), "other_percent": percent}],
        "start_week": start,
        "end_week": start,
    }


def enable(client: TestClient, demo: tuple) -> None:
    url, _, auth = demo
    response = client.put(
        f"{url}/capacity-settings",
        headers=auth["admin"],
        json={"enabled": True, "requester_policy": "everyone"},
    )
    assert response.status_code == 200, response.text


def test_default_disabled_and_settings_permissions(
    capacity: tuple, client: TestClient
) -> None:
    url, employees, auth = capacity
    for role in ("admin", "manager", "employee"):
        assert client.get(f"{url}/capacity-settings", headers=auth[role]).json() == {
            "enabled": False,
            "requester_policy": "everyone",
        }
    for role in ("manager", "employee"):
        assert (
            client.put(
                f"{url}/capacity-settings", headers=auth[role], json={"enabled": True}
            ).status_code
            == 403
        )
    calls = [
        ("get", "/workloads/me", None),
        ("get", "/workloads", None),
        ("put", "/weeks", week_body(employees["E02"].id, 50)),
        (
            "post",
            "/workloads/me/requests",
            {
                "start_week": week_body(capacity[1]["E02"].id, 0)["start_week"],
                "end_week": week_body(capacity[1]["E02"].id, 0)["start_week"],
                "requested_percent": 50,
                "reason": "合成原因",
            },
        ),
        ("get", "/workload-requests", None),
        ("post", f"/workload-requests/{uuid4()}/decision", {"approve": True}),
    ]
    for method, path, body in calls:
        response = client.request(
            method,
            f"{url}/capacity{path}",
            headers=auth["manager"],
            **({"json": body} if body else {}),
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "规划与负荷功能未开启"
    assert (
        client.get(f"{url}/capacity-settings", headers=auth["foreign"]).status_code
        == 404
    )


def test_scope_and_direct_adjustment(
    capacity: tuple, client: TestClient, db: Session
) -> None:
    url, employees, auth = capacity
    enable(client, capacity)
    assert (
        client.get(f"{url}/capacity/workloads", headers=auth["employee"]).status_code
        == 403
    )
    rows = client.get(f"{url}/capacity/workloads", headers=auth["manager"]).json()[
        "data"
    ]
    assert {r["employee_id"] for r in rows} == {
        str(e.id)
        for e in employees.values()
        if e.department_id == employees["E01"].department_id
    }
    assert len(
        client.get(f"{url}/capacity/workloads", headers=auth["admin"]).json()["data"]
    ) == len(employees)
    assert (
        client.get(
            f"{url}/capacity/workloads",
            params={"department_id": str(employees["E04"].department_id)},
            headers=auth["manager"],
        ).status_code
        == 404
    )
    for role, code, expected in [
        ("employee", "E03", 404),
        ("manager", "E04", 404),
        ("manager", "E01", 403),
        ("admin", "E02", 403),
        ("foreign", "E02", 404),
    ]:
        response = client.put(
            f"{url}/capacity/weeks",
            headers=auth[role],
            json=week_body(employees[code].id, 50),
        )
        assert response.status_code == expected
    for value, band in [
        (0, "available"),
        (79, "available"),
        (80, "full"),
        (100, "full"),
        (101, "overloaded"),
        (200, "overloaded"),
    ]:
        response = client.put(
            f"{url}/capacity/weeks",
            headers=auth["manager"],
            json=week_body(employees["E02"].id, value),
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"][0]["confirmed_percent"] == value
        assert response.json()["data"][0]["band"] == band
    for value in [-1, 201, True, 1.5]:
        assert (
            client.put(
                f"{url}/capacity/weeks",
                headers=auth["manager"],
                json=week_body(employees["E02"].id, value),
            ).status_code
            == 422
        )
    # Admin plus a managed-department grant has the same direct-adjustment permission.
    member = db.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == employees["E02"].org_id,
            OrgMembership.employee_id == employees["E02"].id,
        )
    ).one()
    db.add(RoleGrant(org_id=member.org_id, membership_id=member.id, role="org_admin"))
    db.commit()
    assert (
        client.put(
            f"{url}/capacity/weeks",
            headers=auth["employee"],
            json=week_body(employees["E03"].id, 20),
        ).status_code
        == 403
    )
    db.add(
        RoleGrant(
            org_id=member.org_id,
            membership_id=member.id,
            role="dept_manager",
            department_id=employees["E03"].department_id,
        )
    )
    db.commit()
    assert (
        client.put(
            f"{url}/capacity/weeks",
            headers=auth["employee"],
            json=week_body(employees["E03"].id, 20),
        ).status_code
        == 200
    )


def test_employee_admin_cannot_adjust_own_capacity_weeks(
    capacity: tuple, client: TestClient, db: Session
) -> None:
    url, employees, auth = capacity
    enable(client, capacity)
    admin, report = employees["E02"], employees["E03"]
    member = db.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == admin.org_id, OrgMembership.employee_id == admin.id
        )
    ).one()
    db.add(RoleGrant(org_id=member.org_id, membership_id=member.id, role="org_admin"))
    db.commit()
    endpoint = f"{url}/capacity/weeks"
    body = week_body(admin.id, 50)
    report_body = week_body(report.id, 20)
    weeks = select(CapacityWeek).where(
        CapacityWeek.org_id == admin.org_id,
        CapacityWeek.week_start == date.fromisoformat(body["start_week"]),
        col(CapacityWeek.employee_id).in_([admin.id, report.id]),
    )

    assert client.put(endpoint, headers=auth["employee"], json=body).status_code == 403
    assert db.exec(weeks).all() == []

    db.add(
        RoleGrant(
            org_id=member.org_id,
            membership_id=member.id,
            role="dept_manager",
            department_id=admin.department_id,
        )
    )
    db.commit()
    assert client.put(endpoint, headers=auth["employee"], json=body).status_code == 403
    assert db.exec(weeks).all() == []
    mixed = {**body, "items": report_body["items"] + body["items"]}
    assert client.put(endpoint, headers=auth["employee"], json=mixed).status_code == 403
    assert db.exec(weeks).all() == []

    response = client.put(endpoint, headers=auth["employee"], json=report_body)
    assert response.status_code == 200, response.text
    stored = db.exec(weeks).one()
    assert (stored.employee_id, stored.other_percent) == (report.id, 20)


def test_jira_reference_and_platform_percent(
    capacity: tuple, db: Session, client: TestClient
) -> None:
    url, employees, auth = capacity
    enable(client, capacity)
    e = employees["E02"]
    mine = f"{url}/capacity/workloads/me"
    db.execute(delete(ExternalIdentity).where(ExternalIdentity.org_id == e.org_id))
    db.commit()
    assert client.get(mine, headers=auth["employee"]).json()["jira_task_count"] is None
    db.add_all(
        [
            ExternalIdentity(
                org_id=e.org_id, employee_id=e.id, source="jira", external_id=account
            )
            for account in ("account-a", "account-b")
        ]
    )
    for source, resolved, people in [
        ("jira", None, [{"id": "account-a", "role": "assignee"}]),
        (
            "jira",
            None,
            [
                {"id": "account-a", "role": "assignee"},
                {"id": "account-b", "role": "assignee"},
            ],
        ),
        ("jira", None, [{"id": "account-a", "role": "reporter"}]),
        ("jira", "2026-01-01", [{"id": "account-a", "role": "assignee"}]),
        ("jira", None, [{"id": "other", "role": "assignee"}]),
        ("csv:test", None, [{"id": "account-a", "role": "assignee"}]),
    ]:
        db.add(
            Task(
                org_id=e.org_id,
                source=source,
                external_key=str(uuid4()),
                title="合成任务",
                attributes={"resolved": resolved, "people": people},
            )
        )
    demand = CapacityDemand(
        org_id=e.org_id,
        requesting_department_id=e.department_id,
        target_department_id=e.department_id,
        requester_user_id=user_id(db, e),
        capability_key="synthetic",
        capability_name="合成能力",
        headcount=2,
        allocation_percent=50,
        window_start=date.today(),
        window_end=date.today(),
        description="合成需求",
    )
    db.add(demand)
    db.flush()
    for status, percent in [("active", 40), ("active", 20), ("done", 60)]:
        # Independent accepted demands may allocate the same employee concurrently.
        other = CapacityDemand(**(demand.model_dump() | {"id": uuid4()}))
        db.add(other)
        db.flush()
        db.add(
            CapacityAssignment(
                org_id=e.org_id,
                demand_id=other.id,
                employee_id=e.id,
                allocation_percent=percent,
                due_date=week_start(
                    utc_now().astimezone(ZoneInfo("Asia/Shanghai")).date()
                )
                + timedelta(days=4),
                assigned_by_user_id=user_id(db, employees["E01"]),
                status=status,
            )
        )
    db.commit()
    # Historical linked Jira tasks alone do not mean the tenant has connected Jira.
    assert client.get(mine, headers=auth["employee"]).json()["jira_task_count"] is None
    db.add(
        SourceConnection(
            org_id=e.org_id, kind="jira", updated_by_user_id=user_id(db, e)
        )
    )
    db.add(
        ExternalIdentity(
            org_id=e.org_id,
            employee_id=employees["E03"].id,
            source="jira",
            external_id="zero-tasks",
        )
    )
    db.commit()
    row = client.get(mine, headers=auth["employee"]).json()
    assert (
        row["jira_task_count"],
        row["platform_percent"],
        row["confirmed_percent"],
        row["band"],
    ) == (2, 60, None, "unconfirmed")
    client.put(
        f"{url}/capacity/weeks",
        headers=auth["manager"],
        json=week_body(e.id, 50),
    )
    row = client.get(mine, headers=auth["employee"]).json()
    assert (row["jira_task_count"], row["confirmed_percent"], row["band"]) == (
        2,
        110,
        "overloaded",
    )
    assert client.get(mine, headers=auth["manager"]).json()["jira_task_count"] is None
    listing = client.get(f"{url}/capacity/workloads", headers=auth["manager"]).json()[
        "data"
    ]
    assert (
        next(r for r in listing if r["employee_id"] == str(employees["E03"].id))[
            "jira_task_count"
        ]
        == 0
    )


def test_requests_decisions_notifications_and_no_profile_change(
    capacity: tuple, db: Session, client: TestClient
) -> None:
    url, employees, auth = capacity
    enable(client, capacity)
    org_id = employees["E02"].org_id
    for code in ("E01", "E02"):
        db.add(
            ImBinding(
                org_id=org_id,
                employee_id=employees[code].id,
                platform="feishu",
                external_user_id=code,
                external_chat_id=code,
                bot_identity="synthetic-app",
            )
        )
    db.commit()
    dictionary = client.get(f"{url}/capabilities", headers=auth["admin"]).json()
    params = {"capability_key": dictionary["data"][0]["key"]}

    def snapshot() -> list[dict]:
        results = [
            client.get(
                f"{url}/employees/{employees['E02'].id}/profile",
                headers=auth["manager"],
            ),
            client.get(f"{url}/people-search", params=params, headers=auth["manager"]),
            client.get(
                f"{url}/profile-export",
                params={**params, "format": "json"},
                headers=auth["manager"],
            ),
        ]
        assert all(r.status_code == 200 for r in results)
        return [r.json() for r in results]

    before = snapshot()
    pending_url = f"{url}/capacity/workloads/me/requests"
    assert (
        client.post(
            pending_url,
            headers=auth["employee"],
            json={
                "start_week": week_body(capacity[1]["E02"].id, 0)["start_week"],
                "end_week": week_body(capacity[1]["E02"].id, 0)["start_week"],
                "requested_percent": 50,
                "reason": "PRIVATE_REASON",
                "employee_id": str(employees["E03"].id),
            },
        ).status_code
        == 422
    )
    for approve in (True, False):
        response = client.post(
            pending_url,
            headers=auth["employee"],
            json={
                "start_week": week_body(capacity[1]["E02"].id, 0)["start_week"],
                "end_week": week_body(capacity[1]["E02"].id, 0)["start_week"],
                "requested_percent": 80 if approve else 20,
                "reason": "PRIVATE_REASON",
            },
        )
        assert response.status_code == 200, response.text
        row = response.json()
        assert (
            client.post(
                pending_url,
                headers=auth["employee"],
                json={
                    "start_week": week_body(capacity[1]["E02"].id, 0)["start_week"],
                    "end_week": week_body(capacity[1]["E02"].id, 0)["start_week"],
                    "requested_percent": 50,
                    "reason": "合成原因",
                },
            ).status_code
            == 409
        )
        decision_url = f"{url}/capacity/workload-requests/{row['id']}/decision"
        for role in ("employee", "admin", "foreign"):
            assert (
                client.post(
                    decision_url, headers=auth[role], json={"approve": True}
                ).status_code
                == 404
            )
        assert (
            client.get(
                f"{url}/capacity/workload-requests", headers=auth["admin"]
            ).json()["data"]
            == []
        )
        assert client.get(
            f"{url}/capacity/workload-requests", headers=auth["manager"]
        ).json()["data"]
        if not approve:
            assert (
                client.post(
                    decision_url, headers=auth["manager"], json={"approve": False}
                ).status_code
                == 422
            )
        response = client.post(
            decision_url,
            headers=auth["manager"],
            json={"approve": approve, "note": "PRIVATE_NOTE"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == ("approved" if approve else "returned")
        assert (
            client.post(
                decision_url, headers=auth["manager"], json={"approve": True}
            ).status_code
            == 409
        )
        assert (
            client.get(f"{url}/capacity/workloads/me", headers=auth["employee"]).json()[
                "base_percent"
            ]
            == 80
        )
    assert before == snapshot()
    events = db.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == org_id, AuditEvent.action.startswith("capacity.")
        )
    ).all()
    assert {e.action for e in events} >= {
        "capacity.workload_requested",
        "capacity.workload_request_approved",
        "capacity.workload_request_returned",
    }
    assert all(
        "PRIVATE_" not in str(e.details)
        and e.details["via"] == "web"
        and "client_name" in e.details
        for e in events
    )
    items = db.exec(
        select(InboxItem).where(
            InboxItem.org_id == org_id, InboxItem.kind == InboxKind.CAPACITY
        )
    ).all()
    assert len(items) == 4
    reviewer_id = user_id(db, employees["E01"])
    reviewer_items = [i for i in items if i.recipient_user_id == reviewer_id]
    employee_items = [
        i for i in items if i.recipient_employee_id == employees["E02"].id
    ]
    week = week_body(employees["E02"].id, 0)["start_week"]
    assert [i.message for i in reviewer_items] == [
        f"{WORKLOAD_REVIEW_NOTICE}（周次 {week} 至 {week}）。"
        "请前往「待审批 → 负荷与延期」处理。"
    ] * 2
    assert {i.message for i in employee_items} == {
        f"负荷调整申请{result}（周次 {week} 至 {week}）。"
        "请前往「我的负荷」查看处理结果。"
        for result in ("已同意", "已退回")
    }
    assert len(employee_items) == 2
    assert (
        len(
            db.exec(select(ImNotification).where(ImNotification.org_id == org_id)).all()
        )
        == 4
    )
    client.put(
        f"{url}/capacity-settings", headers=auth["admin"], json={"enabled": False}
    )
    assert (
        client.get(f"{url}/capacity/workloads/me", headers=auth["employee"]).status_code
        == 404
    )
    enable(client, capacity)
    assert (
        client.get(f"{url}/capacity/workloads/me", headers=auth["employee"]).json()[
            "base_percent"
        ]
        == 80
    )


def test_delegation_expiry_and_inactive_employee(
    capacity: tuple, db: Session, client: TestClient
) -> None:
    url, employees, auth = capacity
    enable(client, capacity)
    manager, delegate = user_id(db, employees["E01"]), user_id(db, employees["E05"])
    delegation = ReviewDelegation(
        org_id=employees["E01"].org_id,
        manager_user_id=manager,
        delegate_user_id=delegate,
        created_by_user_id=manager,
        starts_at=utc_now() - timedelta(hours=1),
        ends_at=utc_now() + timedelta(hours=1),
    )
    db.add(delegation)
    db.commit()
    pending = client.post(
        f"{url}/capacity/workloads/me/requests",
        headers=auth["employee"],
        json={
            "start_week": week_body(capacity[1]["E02"].id, 0)["start_week"],
            "end_week": week_body(capacity[1]["E02"].id, 0)["start_week"],
            "requested_percent": 70,
            "reason": "合成原因",
        },
    ).json()
    delegate_headers = headers(db, employees["E05"])
    decision = f"{url}/capacity/workload-requests/{pending['id']}/decision"
    assert client.get(
        f"{url}/capacity/workload-requests", headers=delegate_headers
    ).json()["data"]
    delegation.ends_at = utc_now() - timedelta(minutes=1)
    db.add(delegation)
    db.commit()
    assert (
        client.post(
            decision, headers=delegate_headers, json={"approve": True}
        ).status_code
        == 404
    )
    delegation.ends_at = utc_now() + timedelta(hours=1)
    db.add(delegation)
    db.commit()
    row = client.post(decision, headers=delegate_headers, json={"approve": True}).json()
    assert row["decided_for_user_id"] == str(manager)
    assert row["decided_by_user_id"] == str(delegate)
    assert client.get(
        f"{url}/capacity/workload-requests", headers=delegate_headers
    ).json()["data"]
    employees["E02"].employment_status = "inactive"
    db.add(employees["E02"])
    db.commit()
    assert (
        client.put(
            f"{url}/capacity/weeks",
            headers=auth["manager"],
            json=week_body(employees["E02"].id, 10),
        ).status_code
        == 404
    )


def test_tools_use_shared_services() -> None:
    assert [(t.name, t.writes) for t in CAPACITY_TOOLS] == [
        ("list_capacity_departments", False),
        ("capacity_reference", False),
        ("submit_demand", True),
        ("list_demands", False),
        ("get_demand", False),
        ("list_projects", False),
        ("get_project", False),
        ("get_demand_basis", False),
        ("reply_demand", True),
        ("respond_demand", True),
        ("get_my_workload", False),
        ("request_workload_change", True),
        ("list_my_assignments", False),
        ("request_extension", True),
    ]


def test_concurrent_requests_and_decisions(capacity: tuple, client: TestClient) -> None:
    url, _, auth = capacity
    enable(client, capacity)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda _: client.post(
                    f"{url}/capacity/workloads/me/requests",
                    headers=auth["employee"],
                    json={
                        "start_week": week_body(capacity[1]["E02"].id, 0)["start_week"],
                        "end_week": week_body(capacity[1]["E02"].id, 0)["start_week"],
                        "requested_percent": 30,
                        "reason": "合成并发申请",
                    },
                ),
                range(2),
            )
        )
    assert sorted(r.status_code for r in responses) == [200, 409]
    request_id = next(r.json()["id"] for r in responses if r.status_code == 200)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda _: client.post(
                    f"{url}/capacity/workload-requests/{request_id}/decision",
                    headers=auth["manager"],
                    json={"approve": True},
                ),
                range(2),
            )
        )
    assert sorted(r.status_code for r in responses) == [200, 409]


def test_direct_adjustment_notifies_employee(
    capacity: tuple, db: Session, client: TestClient
) -> None:
    url, employees, auth = capacity
    enable(client, capacity)
    subject = employees["E02"]
    db.add(
        ImBinding(
            org_id=subject.org_id,
            employee_id=subject.id,
            platform="feishu",
            external_user_id="synthetic",
            external_chat_id="synthetic",
            bot_identity="synthetic",
        )
    )
    db.commit()
    result = client.put(
        f"{url}/capacity/weeks",
        headers=auth["manager"],
        json=week_body(subject.id, 20),
    )
    assert result.status_code == 200
    item = db.exec(
        select(InboxItem).where(
            InboxItem.org_id == subject.org_id, InboxItem.kind == InboxKind.CAPACITY
        )
    ).one()
    week = week_body(subject.id, 20)["start_week"]
    assert item.recipient_employee_id == subject.id
    assert item.message == (
        f"团队负荷已更新（周次 {week} 至 {week}）。请前往「我的负荷」核对。"
    )
    assert db.exec(
        select(ImNotification).where(
            ImNotification.org_id == subject.org_id,
            ImNotification.inbox_item_id == item.id,
        )
    ).one()
    audit = db.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == subject.org_id,
            AuditEvent.action == "capacity.workload_adjusted",
        )
    ).one()
    assert audit.details == {
        "before_percent": None,
        "after_percent": 20,
        "week_start": week_body(subject.id, 20)["start_week"],
        "via": "web",
        "client_name": None,
    }


@pytest.mark.parametrize("loss", ["grant", "delegation", "department"])
def test_processed_requests_require_current_authority(
    capacity: tuple, db: Session, client: TestClient, loss: str
) -> None:
    url, employees, auth = capacity
    enable(client, capacity)
    manager = user_id(db, employees["E01"])
    actor = auth["manager"]
    delegation = ReviewDelegation(
        org_id=employees["E01"].org_id,
        manager_user_id=manager,
        delegate_user_id=user_id(db, employees["E05"]),
        created_by_user_id=manager,
        starts_at=utc_now() - timedelta(hours=1),
        ends_at=utc_now() + timedelta(hours=1),
    )
    if loss == "delegation":
        db.add(delegation)
        db.commit()
        actor = headers(db, employees["E05"])
    response = client.post(
        f"{url}/capacity/workloads/me/requests",
        headers=auth["employee"],
        json={
            "start_week": week_body(capacity[1]["E02"].id, 0)["start_week"],
            "end_week": week_body(capacity[1]["E02"].id, 0)["start_week"],
            "requested_percent": 40,
            "reason": "PRIVATE_HISTORY_REASON",
        },
    )
    assert response.status_code == 200
    request_id = response.json()["id"]
    decision = f"{url}/capacity/workload-requests/{request_id}/decision"
    assert (
        client.post(decision, headers=actor, json={"approve": True}).status_code == 200
    )
    listing = f"{url}/capacity/workload-requests"
    assert [r["id"] for r in client.get(listing, headers=actor).json()["data"]] == [
        request_id
    ]
    if loss == "grant":
        member = db.exec(
            select(OrgMembership).where(
                OrgMembership.org_id == employees["E01"].org_id,
                OrgMembership.user_id == manager,
            )
        ).one()
        db.execute(
            delete(RoleGrant).where(
                RoleGrant.org_id == member.org_id,
                RoleGrant.membership_id == member.id,
                RoleGrant.role == "dept_manager",
            )
        )
    elif loss == "delegation":
        delegation.ends_at = utc_now() - timedelta(minutes=1)
        db.add(delegation)
    else:
        employees["E02"].department_id = employees["E04"].department_id
        db.add(employees["E02"])
    db.commit()
    result = client.get(listing, headers=actor)
    assert result.json() == {"data": [], "count": 0}
    assert "PRIVATE_HISTORY_REASON" not in result.text
    assert (
        client.post(decision, headers=actor, json={"approve": True}).status_code == 404
    )
    # The requester retains access to their own history through the self endpoint.
    own = client.get(f"{url}/capacity/workloads/me", headers=auth["employee"])
    assert own.json()["requests"][0]["id"] == request_id
    if loss == "delegation":
        # The original reviewer may see decisions made on their behalf while authorized.
        assert [
            r["id"] for r in client.get(listing, headers=auth["manager"]).json()["data"]
        ] == [request_id]
