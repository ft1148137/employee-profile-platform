import json
from collections.abc import Generator
from datetime import UTC, datetime, time, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi.testclient import TestClient
from pydantic import JsonValue
from sqlalchemy import delete
from sqlmodel import Session, select

from app.authz.deps import get_org_principal
from app.authz.principal import Principal
from app.domains.admin_assistant import tools as admin_tools
from app.domains.admin_assistant.registry import TableCard
from app.domains.admin_assistant.schemas import (
    ChangeSetCard,
    FileUploadCard,
    LinkCard,
    SecretInputCard,
)
from app.domains.base import utc_now
from app.domains.bot import core, store, tools
from app.domains.bot import schemas as bot_schemas
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
from tests.api.routes.test_m5_people_search_tools import grant_admin
from tests.api.routes.test_m5_people_search_tools import people as people
from tests.api.routes.test_m6_capacity_mcp import denied, listing, use
from tests.api.routes.test_m6_capacity_workload import enable, user_id
from tests.api.routes.test_m10_project import project_data as project_data
from tests.api.routes.test_mcp import call as mcp_call
from tests.api.routes.test_mcp import initialize, token_for
from tests.domains.test_bot_core import enable as enable_bot
from tests.utils.pi import PiHarness


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


@pytest.fixture
def replied_project(project_data: tuple, client: TestClient) -> dict[str, Any]:
    base, auth, body, _ = project_data
    created = client.post(f"{base}/projects", headers=auth["employee"], json=body)
    assert created.status_code == 200, created.text
    summary = created.json()
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
    return summary


def test_admin_project_reads(
    replied_project: dict[str, Any],
    project_data: tuple,
    demo: tuple,
    db: Session,
    client: TestClient,
) -> None:
    base, auth, _, actors = project_data
    admin = admin_principal(db, demo)
    assert admin.is_org_admin and admin.employee_id is None
    project_id = replied_project["id"]
    args = {"project_id": project_id}
    cards: list[
        LinkCard | TableCard | ChangeSetCard | SecretInputCard | FileUploadCard
    ] = []

    def query(
        principal: Principal, function: str, arguments: dict[str, JsonValue]
    ) -> JsonValue:
        return admin_tools.call(
            db,
            principal,
            cards,
            "admin_read",
            {"function": function, "args": arguments},
        )

    listing_url = f"{base}/projects"
    web_list = client.get(listing_url, headers=auth["admin"])
    assert web_list.status_code == 200, web_list.text
    assert web_list.json() == [replied_project]
    assert query(admin, "list_projects", {}) == {
        "data": {"data": [replied_project], "count": 1}
    }
    _, table = cards
    assert isinstance(table, TableCard)
    assert table.page == "/capacity" and table.total == 1
    assert table.rows == [[replied_project[field] for field in table.columns]]

    cards.clear()
    web_detail = client.get(f"{listing_url}/{project_id}", headers=auth["admin"])
    assert web_detail.status_code == 200, web_detail.text
    detail = web_detail.json()
    assert query(admin, "get_project", args) == {"data": detail}
    assert detail["roles"][0]["outcome"] == "can_do"
    assert detail["roles"][0]["demand"]["events"][-1]["responder_name"] is None
    assert detail["schedule"]["delay_days"] > 0
    assert detail["schedule"]["impacts"][0]["downstream"]
    assert cards == [
        LinkCard(
            title="打开对应页面", page=f"/capacity?tab=projects&project={project_id}"
        )
    ]

    foreign_user = db.get_one(
        User,
        UUID(
            jwt.decode(
                auth["foreign"]["Authorization"].split()[1],
                options={"verify_signature": False},
            )["sub"]
        ),
    )
    foreign_org = db.exec(
        select(OrgMembership.org_id).where(OrgMembership.user_id == foreign_user.id)
    ).one()
    grant_admin(db, get_org_principal(foreign_org, foreign_user, db))
    db.add(
        CapacitySetting(
            org_id=foreign_org, enabled=True, updated_by_user_id=foreign_user.id
        )
    )
    db.flush()
    foreign_admin = get_org_principal(foreign_org, foreign_user, db)
    assert foreign_admin.is_org_admin
    cards.clear()
    assert query(foreign_admin, "get_project", args) == {
        "data": {"error": "找不到该记录"}
    }
    assert not cards
    assert query(foreign_admin, "list_projects", {}) == {
        "data": {"data": [], "count": 0}
    }
    db.rollback()

    cards.clear()
    employee = admin_principal(db, demo, "employee")
    manager = get_org_principal(admin.org_id, db.get_one(User, actors[0]), db)
    assert manager.managed_department_ids and not manager.is_org_admin
    queries = (("list_projects", {}), ("get_project", args))
    for function, arguments in queries:
        for non_admin in (employee, manager):
            assert query(non_admin, function, arguments) == {
                "data": {"error": "需要企业管理员权限"}
            }
    assert not cards

    setting = db.exec(
        select(CapacitySetting).where(CapacitySetting.org_id == admin.org_id)
    ).one()
    setting.enabled = False
    db.commit()
    for function, arguments in queries:
        assert query(admin, function, arguments) == {
            "data": {"error": "需求与容量功能已关闭，如需使用请先在能力与规则里开启"}
        }
        assert not cards


@pytest.mark.parametrize("channel", ["web", "feishu"])
def test_project_reads_share_web_permissions_and_feature_gate(
    replied_project: dict[str, Any],
    project_data: tuple,
    demo: tuple,
    db: Session,
    client: TestClient,
    channel: Literal["web", "feishu"],
) -> None:
    base, auth, body, actors = project_data
    _, employees, _ = demo
    manager, requester = employees["E01"], employees["E02"]
    summary = replied_project
    args = {"project_id": summary["id"]}
    detail_url = f"{base}/projects/{summary['id']}"

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
    cards: list[bot_schemas.Message] = []

    def query(
        principal: Principal,
        session: BotSession,
        name: str,
        arguments: dict[str, JsonValue],
    ) -> JsonValue:
        return tools.call(
            db,
            principal,
            session,
            cards,
            "coordination",
            {"function": name, "arguments": arguments},
        )

    for employee, role, expected in (
        (unrelated, "b3", empty_list),
        (b_manager, "b0", empty_list),
        (requester, "employee", expected_list),
        (manager, "manager", expected_list),
    ):
        principal = get_org_principal(
            employee.org_id, db.get_one(User, user_id(db, employee)), db
        )
        session = BotSession(
            org_id=principal.org_id, employee_id=employee.id, channel=channel
        )
        mcp = initialize(client, token_for(db, employee)[0])
        bot_names = {
            t.name for t in tools.definitions(principal, db=db, channel=channel)
        }
        assert "coordination" in bot_names and names.isdisjoint(bot_names)
        assert names <= listing(client, mcp).keys()
        assert query(principal, session, "list_projects", {}) == expected
        assert use(client, mcp, "list_projects", {}) == expected
        bot_detail = query(principal, session, "get_project", args)
        assert not cards
        for name, arguments in (("list_projects", {}), ("get_project", args)):
            assert tools.call(db, principal, session, cards, name, arguments) == {
                "error": "请通过 coordination 调用此工具"
            }
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
    assert (names | {"coordination"}).isdisjoint(
        t.name for t in tools.definitions(principal, db=db, channel=channel)
    )
    assert names.isdisjoint(listing(client, mcp))
    for name, arguments in (("list_projects", {}), ("get_project", args)):
        assert query(principal, session, name, arguments) == {
            "error": "不允许调用此工具"
        }
        denied(mcp_call(client, mcp, name, arguments), 404)


@pytest.mark.parametrize("channel", ["web", "feishu"])
def test_pi_admin_manager_project_queries_and_confirmation(
    project_data: tuple,
    demo: tuple,
    db: Session,
    client: TestClient,
    pi_service: PiHarness,
    monkeypatch: pytest.MonkeyPatch,
    channel: Literal["web", "feishu"],
) -> None:
    base, auth, body, _ = project_data
    manager = demo[1]["E01"]
    org_id = manager.org_id
    user = db.get_one(User, user_id(db, manager))
    grant_admin(db, get_org_principal(org_id, user, db))
    principal = get_org_principal(org_id, user, db)
    assert principal.is_org_admin and principal.managed_department_ids
    created = client.post(f"{base}/projects", headers=auth["manager"], json=body)
    assert created.status_code == 200, created.text
    project_id = created.json()["id"]

    enable_bot(db, org_id)
    monkeypatch.setattr(core, "model_config", lambda _: pi_service.config)
    session = store.create_session(db, principal, channel)
    demand_args = {
        key: value
        for key, value in body["roles"][1].items()
        if key not in {"role_name", "depends_on"}
    } | {"requesting_department_id": body["requesting_department_id"]}
    pi_service.replies = [
        {
            "calls": [
                {
                    "name": "coordination",
                    "arguments": {
                        "function": "list_projects",
                        "arguments": {},
                    },
                }
            ]
        },
        {
            "calls": [
                {
                    "name": "coordination",
                    "arguments": {
                        "function": "get_project",
                        "arguments": {"project_id": project_id},
                    },
                }
            ]
        },
        {"calls": [{"name": "prepare_demand", "arguments": demand_args}]},
        "请核对并确认需求",
    ]
    turn = core.turn(db, principal, session.id, "查看项目进度，再起草一条需求")
    assert len(turn.steps) == 3 and all(step.ok for step in turn.steps)
    card = next(m for m in turn.messages if isinstance(m, bot_schemas.CapacityCard))
    assert card.status == "pending"

    request = pi_service.requests[0]["body"]
    definitions = {t["function"]["name"]: t["function"] for t in request["tools"]}
    assert len(definitions) == len(request["tools"]) == 29
    assert {"list_projects", "get_project"}.isdisjoint(definitions)
    coordination = definitions["coordination"]
    assert {"list_projects", "get_project"} <= set(
        coordination["parameters"]["properties"]["function"]["enum"]
    )
    contracts = json.loads(coordination["description"].splitlines()[1])
    assert contracts["list_projects"]["parameters"]["properties"] == {}
    assert contracts["list_projects"]["parameters"]["additionalProperties"] is False
    detail_schema = contracts["get_project"]["parameters"]
    assert detail_schema["required"] == ["project_id"]
    assert detail_schema["properties"]["project_id"]["format"] == "uuid"
    assert "/capacity?tab=projects&project={id}" in str(
        request["messages"][0]["content"]
    )

    for index in (1, 2):
        result = pi_service.requests[index]["body"]["messages"][-1]
        assert result["role"] == "tool" and project_id in result["content"]
    confirmed = core.act(db, principal, session.id, card.actions[0])
    assert isinstance(confirmed.messages[0], bot_schemas.CapacityCard)
    assert confirmed.messages[0].status == "confirmed"


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
