import json
from collections.abc import Generator
from datetime import UTC, datetime, time, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlmodel import Session, select

from app.authz.deps import get_org_principal
from app.domains.admin_assistant.registry import TableCard
from app.domains.base import utc_now
from app.domains.bot import tools
from app.domains.bot.models import BotSession
from app.domains.capacity import calendar
from app.domains.capacity.models import (
    CapacityAssignment,
    CapacityDemand,
    CapacityProject,
    CapacityProposal,
    CapacityProposalLine,
    CapacitySetting,
    CapacityUnavailability,
    CapacityWeek,
    ProposalStatus,
)
from app.domains.capacity.project import PROJECT_NOT_FOUND
from app.domains.org.models import Department, Employee, OrgMembership
from app.models import User
from tests.admin_assistant.test_registry import call as admin_call
from tests.admin_assistant.test_registry import principal as admin_principal
from tests.api.routes.test_m5_people_search_tools import people as people
from tests.api.routes.test_m6_capacity_mcp import denied, listing, use
from tests.api.routes.test_m6_capacity_workload import enable, user_id
from tests.api.routes.test_m10_project import project_data as project_data
from tests.api.routes.test_mcp import call as mcp_call
from tests.api.routes.test_mcp import initialize, token_for


def test_admin_find_people_reads_only_published_matches(
    people: tuple, db: Session, client: TestClient
) -> None:
    demo, capability, now, foreign_department = people
    url, employees, auth = demo
    admin = admin_principal(db, demo)
    filters = {
        "capability_key": capability["key"],
        "minimum_level": "independent_complex",
        "statuses": ["confirmed"],
        "evidence_from": (now - timedelta(days=31)).date().isoformat(),
        "evidence_to": (now - timedelta(days=29)).date().isoformat(),
        "department_id": str(employees["E03"].department_id),
    }
    result = admin_call(db, admin, "find_people", filters)
    web = client.get(f"{url}/people-search", headers=auth["admin"], params=filters)
    assert web.status_code == 200, web.text
    assert result["data"] == web.json() and result["data"]["count"] == 1
    row = result["data"]["data"][0]
    assert row["employee_id"] == str(employees["E03"].id)
    assert row["statement"] == "合成已发布陈述"
    card = TableCard.model_validate(result["cards"][0])
    assert (card.page, card.total, card.rows) == (
        "/employees",
        1,
        [[row[field] for field in card.columns]],
    )
    assert "SECRET" not in json.dumps(result)
    assert admin_call(
        db,
        admin_principal(db, demo, "employee"),
        "find_people",
        filters,
    ) == {"data": {"error": "需要企业管理员权限"}}
    foreign = admin_call(
        db,
        admin,
        "find_people",
        {**filters, "department_id": str(foreign_department.id)},
    )
    assert foreign["data"] == {"data": [], "count": 0}


def test_project_reads_share_web_permissions_and_feature_gate(
    project_data: tuple, demo: tuple, db: Session, client: TestClient
) -> None:
    base, auth, body, actors = project_data
    _, employees, _ = demo
    manager, requester = employees["E01"], employees["E02"]
    created = client.post(f"{base}/projects", headers=auth["employee"], json=body)
    assert created.status_code == 200, created.text
    summary = created.json()
    args = {"project_id": summary["id"]}
    detail_url = f"{base}/projects/{summary['id']}"
    first_role = client.get(detail_url, headers=auth["employee"]).json()["roles"][0]
    reply = client.post(
        f"{base}/demands/{first_role['id']}/reply",
        headers=auth["b0"],
        json={
            "outcome": "can_do",
            "revision": 1,
            "start_date": "2026-12-01",
            "end_date": "2026-12-05",
            "text": "合成交付答复",
        },
    )
    assert reply.status_code == 200, reply.text

    b_manager, unrelated = [
        db.get_one(
            Employee,
            db.exec(select(OrgMembership).where(OrgMembership.user_id == actor))
            .one()
            .employee_id,
        )
        for actor in (actors[0], actors[-1])
    ]
    expected_list = {"data": [summary], "count": 1}
    empty_list = {"data": [], "count": 0}
    names = {"list_projects", "get_project"}
    for employee, role, expected in (
        (unrelated, "b3", empty_list),
        (b_manager, "b0", empty_list),
        (requester, "employee", expected_list),
        (manager, "manager", expected_list),
    ):
        principal = get_org_principal(
            employee.org_id, db.get_one(User, user_id(db, employee)), db
        )
        session = BotSession(org_id=principal.org_id, employee_id=employee.id)
        mcp = initialize(client, token_for(db, employee)[0])
        assert names <= {
            definition.name for definition in tools.definitions(principal, db=db)
        }
        assert names <= listing(client, mcp).keys()
        assert tools.call(db, principal, session, [], "list_projects", {}) == expected
        assert use(client, mcp, "list_projects", {}) == expected
        bot_detail = tools.call(db, principal, session, [], "get_project", args)
        web = client.get(detail_url, headers=auth[role])
        if role == "b3":
            assert web.status_code == 404
            assert bot_detail == {"error": PROJECT_NOT_FOUND}
            denied(mcp_call(client, mcp, "get_project", args), 404)
            continue
        assert web.status_code == 200, web.text
        detail = web.json()
        assert bot_detail == use(client, mcp, "get_project", args) == detail
        assert detail["roles"][0]["outcome"] == "can_do"
        assert detail["schedule"]["delay_days"] > 0
        assert detail["schedule"]["impacts"][0]["downstream"]
        assert (
            b_manager.display_name not in web.text and str(b_manager.id) not in web.text
        )
        if role == "b0":
            assert detail["description"] is None
            assert all(row["demand"] is None for row in detail["roles"])
            assert "合成交付答复" not in web.text
        else:
            assert detail["description"] == body["description"]
            assert detail["roles"][0]["demand"]["events"][-1]["responder_name"] is None

    assert client.get(detail_url, headers=auth["foreign"]).status_code == 404
    setting = db.exec(
        select(CapacitySetting).where(CapacitySetting.org_id == manager.org_id)
    ).one()
    setting.enabled = False
    db.add(setting)
    db.commit()
    assert names.isdisjoint(
        definition.name for definition in tools.definitions(principal, db=db)
    )
    assert names.isdisjoint(listing(client, mcp))
    for name, arguments in (("list_projects", {}), ("get_project", args)):
        assert tools.call(db, principal, session, [], name, arguments) == {
            "error": "需求与容量模块未开启"
        }
        denied(mcp_call(client, mcp, name, arguments), 404)


@pytest.fixture
def workload_demo(demo: tuple, db: Session, client: TestClient) -> Generator[tuple]:
    enable(client, demo)
    yield demo
    db.rollback()
    org_id = demo[1]["E01"].org_id
    for model in (
        CapacityAssignment,
        CapacityProposalLine,
        CapacityProposal,
        CapacityDemand,
        CapacityProject,
        CapacityUnavailability,
        CapacityWeek,
        CapacitySetting,
    ):
        db.execute(delete(model).where(model.org_id == org_id))
    db.commit()


def test_workload_segments_window_scope_and_weekly_totals(
    workload_demo: tuple, db: Session, client: TestClient
) -> None:
    url, employees, auth = workload_demo
    manager, employee, outside = (employees[code] for code in ("E01", "E02", "E04"))
    org_id = manager.org_id
    actor = user_id(db, manager)
    dept = db.get_one(Department, manager.department_id)
    child = Department(org_id=org_id, name="合成下级部门", parent_department_id=dept.id)
    db.add(child)
    db.flush()
    employee.department_id = child.id
    now = utc_now()
    start = calendar.this_week(db, org_id)
    end = start + timedelta(weeks=1)
    project = CapacityProject(
        org_id=org_id,
        requesting_department_id=dept.id,
        requester_user_id=actor,
        title="合成项目",
        description="PRIVATE_PROJECT",
    )
    db.add(project)
    db.flush()
    demands = [
        CapacityDemand(
            org_id=org_id,
            requesting_department_id=dept.id,
            target_department_id=outside.department_id,
            requester_user_id=actor,
            capability_key="synthetic",
            capability_name="合成能力",
            headcount=1,
            allocation_percent=20,
            window_start=start,
            window_end=end,
            description="PRIVATE_DEMAND",
            project_id=project.id if i == 1 else None,
            role_name="合成角色" if i == 1 else None,
        )
        for i in range(7)
    ]
    db.add_all(demands)
    db.flush()
    lines = []
    for i, expires, status, assignee, starts in (
        (1, now + timedelta(days=1), ProposalStatus.COMMITTED, employee, start),
        (2, now + timedelta(days=1), ProposalStatus.PENDING, employee, start),
        (3, now - timedelta(days=1), ProposalStatus.PENDING, employee, start),
        (4, now + timedelta(days=1), ProposalStatus.WITHDRAWN, employee, start),
        (5, now + timedelta(days=1), ProposalStatus.PENDING, outside, start),
        (6, now + timedelta(days=1), ProposalStatus.PENDING, employee, end),
    ):
        proposal = CapacityProposal(
            org_id=org_id,
            demand_id=demands[i].id,
            source_option_id=uuid4(),
            demand_revision=1,
            proposed_by_user_id=actor,
            hold_expires_at=expires,
            status=status,
        )
        db.add(proposal)
        db.flush()
        line = CapacityProposalLine(
            org_id=org_id,
            proposal_id=proposal.id,
            employee_id=assignee.id,
            lender_department_id=child.id if i in (1, 2) else None,
            allocation_percent=20,
            start_date=starts,
            end_date=end,
        )
        db.add(line)
        lines.append(line)
    db.flush()
    assignments = [
        CapacityAssignment(
            org_id=org_id,
            demand_id=demands[i].id,
            employee_id=assignee.id,
            assigned_by_user_id=actor,
            allocation_percent=20,
            start_date=starts,
            due_date=due,
            status=status,
            proposal_line_id=lines[0].id if i == 1 else None,
            created_at=datetime.combine(start, time.min, UTC),
        )
        for i, assignee, starts, due, status in (
            (0, employee, None, start, "active"),
            (1, employee, start, end, "active"),
            (2, employee, end, end, "active"),
            (3, employee, start, start, "done"),
            (4, outside, start, end, "active"),
        )
    ]
    unavailable = [
        CapacityUnavailability(
            org_id=org_id,
            employee_id=assignee.id,
            created_by_user_id=actor,
            start_date=day,
            end_date=day,
        )
        for assignee, day in ((employee, start), (employee, end), (outside, start))
    ]
    db.add_all([*assignments, *unavailable])
    db.add(
        CapacityWeek(
            org_id=org_id,
            employee_id=employee.id,
            week_start=start,
            other_percent=30,
            confirmed_by_user_id=actor,
        )
    )
    db.commit()

    base = f"{url}/capacity/workloads"
    params = {"from_week": start.isoformat(), "weeks": 1}
    response = client.get(base, headers=auth["manager"], params=params)
    assert response.status_code == 200, response.text
    rows = {row["employee_id"]: row for row in response.json()["data"]}
    assert (
        str(outside.id) not in rows and str(lines[4].proposal_id) not in response.text
    )
    assert "PRIVATE_" not in response.text
    row = rows[str(employee.id)]
    assert (rows[str(manager.id)]["assignments"], rows[str(manager.id)]["holds"]) == (
        [],
        [],
    )
    committed, borrowed = (
        next(s for s in row["assignments"] if s["id"] == str(a.id))
        for a in assignments[:2]
    )
    assert len(row["assignments"]) == 2
    assert committed == {
        "id": str(assignments[0].id),
        "demand_id": str(demands[0].id),
        "kind": "committed",
        "title": "合成能力",
        "capability_name": "合成能力",
        "requesting_department_name": dept.name,
        "allocation_percent": 20,
        "start_date": start.isoformat(),
        "due_date": start.isoformat(),
        "legacy_start": True,
    }
    assert (borrowed["kind"], borrowed["title"], borrowed["legacy_start"]) == (
        "borrow",
        "合成项目 · 合成角色",
        False,
    )
    assert row["holds"] == [
        {
            "proposal_id": str(lines[1].proposal_id),
            "demand_id": str(demands[2].id),
            "title": "合成能力",
            "capability_name": "合成能力",
            "requesting_department_name": dept.name,
            "is_borrow": True,
            "allocation_percent": 20,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "expires_at": (now + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
        }
    ]
    assert row["unavailabilities"] == [
        {
            "id": str(unavailable[0].id),
            "start_date": start.isoformat(),
            "end_date": start.isoformat(),
        }
    ]
    assert row["band"] == "overloaded"
    assert len(row["weeks"]) == 1
    assert {
        key: row["weeks"][0][key]
        for key in ("capacity_percent", "platform_percent", "hold_percent")
    } == {"capacity_percent": 80, "platform_percent": 60, "hold_percent": 20}
    mine = client.get(f"{base}/me", headers=auth["employee"], params=params)
    assert mine.status_code == 200, mine.text
    assert {key: mine.json()[key] for key in row} == row
    assert client.get(base, headers=auth["employee"], params=params).status_code == 403
    assert client.get(base, headers=auth["foreign"], params=params).status_code == 404
    assert (
        client.get(
            base,
            headers=auth["manager"],
            params={**params, "department_id": str(outside.department_id)},
        ).status_code
        == 404
    )
