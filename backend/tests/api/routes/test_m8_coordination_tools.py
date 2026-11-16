import json
from collections.abc import Generator
from datetime import timedelta
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlmodel import Session, select

from app.authz.principal import Principal
from app.core.security import create_access_token
from app.domains.capacity.calendar import this_week
from app.domains.capacity.models import (
    CapacityDemand,
    CapacityDemandEvent,
    CapacitySetting,
    CapacityWeek,
    DemandEventKind,
)
from app.domains.org.models import Employee, OrgMembership, RoleGrant
from app.domains.planning.models import PlanningCase, PlanningOption, PlanningSnapshot
from app.domains.planning.tools import COORDINATION_TOOLS
from app.models import User
from tests.api.routes.test_m7_planning_readonly import business_rows
from tests.domains.test_im_gateway import principal

TOOLS = {tool.name: tool for tool in COORDINATION_TOOLS}


@pytest.fixture
def coordination(demo: tuple, db: Session, client: TestClient) -> Generator[tuple]:
    url, people, auth = demo
    org_id = people["E01"].org_id
    members = {
        m.employee_id: m
        for m in db.exec(
            select(OrgMembership).where(OrgMembership.org_id == org_id)
        ).all()
    }
    for code in ("E04", "E10"):
        if people[code].id not in members:
            user = User(
                username=str(uuid4()),
                email=f"{uuid4()}@example.com",
                hashed_password="unused",
            )
            db.add(user)
            db.flush()
            member = OrgMembership(
                org_id=org_id, user_id=user.id, employee_id=people[code].id
            )
            db.add(member)
            db.flush()
            members[people[code].id] = member
            auth[code] = {
                "Authorization": f"Bearer {create_access_token(user.id, expires_delta=timedelta(minutes=10))}"
            }
    foreign_user = UUID(
        jwt.decode(
            auth["foreign"]["Authorization"].split()[1],
            options={"verify_signature": False},
        )["sub"]
    )
    foreign_member = db.exec(
        select(OrgMembership).where(OrgMembership.user_id == foreign_user)
    ).one()
    foreign_person = Employee(
        org_id=foreign_member.org_id, display_name="合成外企业员工"
    )
    db.add(foreign_person)
    db.flush()
    foreign_member.employee_id = foreign_person.id
    db.add(foreign_member)
    people["foreign"] = foreign_person
    db.add(
        CapacitySetting(
            org_id=foreign_member.org_id, enabled=True, updated_by_user_id=foreign_user
        )
    )
    grants = []
    for holder, target in (("E01", "E01"), ("E05", "E04"), ("E10", "E10")):
        membership_id = members[people[holder].id].id
        department_id = people[target].department_id
        existing = db.exec(
            select(RoleGrant).where(
                RoleGrant.membership_id == membership_id,
                RoleGrant.role == "dept_manager",
                RoleGrant.department_id == department_id,
            )
        ).first()
        if not existing:
            grant = RoleGrant(
                org_id=org_id,
                membership_id=membership_id,
                role="dept_manager",
                department_id=department_id,
            )
            db.add(grant)
            grants.append(grant.id)
    db.add(
        CapacitySetting(
            org_id=org_id, enabled=True, updated_by_user_id=members[None].user_id
        )
    )
    start = this_week(db, org_id)
    capability = client.get(f"{url}/capabilities", headers=auth["admin"]).json()[
        "data"
    ][0]
    row = CapacityDemand(
        org_id=org_id,
        requesting_department_id=people["E01"].department_id,
        requester_user_id=members[people["E01"].id].user_id,
        target_department_id=people["E04"].department_id,
        capability_key=capability["key"],
        capability_name=capability["name"],
        minimum_level=None,
        headcount=1,
        allocation_percent=50,
        window_start=start,
        window_end=start + timedelta(days=13),
        description="合成需求",
    )
    db.add(row)
    for code in ("E04", "E05"):
        for week in (start, start + timedelta(days=7)):
            db.add(
                CapacityWeek(
                    org_id=org_id,
                    employee_id=people[code].id,
                    week_start=week,
                    other_percent=10,
                    confirmed_by_user_id=members[None].user_id,
                )
            )
    db.commit()
    yield people, auth, row
    db.rollback()
    db.execute(delete(RoleGrant).where(RoleGrant.id.in_(grants)))
    for model in (
        PlanningOption,
        PlanningSnapshot,
        PlanningCase,
        CapacityWeek,
        CapacityDemandEvent,
        CapacityDemand,
        CapacitySetting,
    ):
        db.execute(delete(model).where(model.org_id == org_id))
    db.execute(
        delete(CapacitySetting).where(CapacitySetting.org_id == foreign_member.org_id)
    )
    db.commit()


def invoke(name: str, p: Principal, db: Session, **kwargs: object) -> dict:
    tool = TOOLS[name]
    return tool.handler(p, db, tool.input_model.model_validate(kwargs)).model_dump(
        mode="json"
    )


def assert_no_b_people(value: object, people: dict) -> None:
    raw = json.dumps(value, ensure_ascii=False, default=str)
    for code in ("E04", "E05"):
        assert people[code].display_name not in raw
        assert str(people[code].id) not in raw
        assert people[code].employee_code not in raw


def test_role_scope_and_readonly(coordination: tuple, db: Session) -> None:
    people, _, row = coordination
    a = principal(db, row.org_id, people["E01"].id)
    employee = principal(db, row.org_id, people["E02"].id)
    b = principal(db, row.org_id, people["E05"].id)
    outside = principal(db, row.org_id, people["E10"].id)
    worker = principal(db, row.org_id, people["E04"].id)
    foreign = principal(db, people["foreign"].org_id, people["foreign"].id)
    before = business_rows(db, row.org_id)
    for viewer in (a,):
        for name in (
            "read_coordination_context",
            "inspect_capacity",
            "search_qualified_candidates",
            "get_approval_status",
        ):
            result = invoke(name, viewer, db, demand_id=row.id)
            assert_no_b_people(result, people)
        assert invoke("simulate_plan", viewer, db, demand_id=row.id) == {
            "error": "无权查看方案"
        }
    for viewer in (employee, worker, outside, foreign):
        for name in (
            "read_coordination_context",
            "inspect_capacity",
            "search_qualified_candidates",
            "get_approval_status",
        ):
            with pytest.raises(HTTPException):
                invoke(name, viewer, db, demand_id=row.id)
    assert business_rows(db, row.org_id) == before
    context = invoke("read_coordination_context", b, db, demand_id=row.id)
    assert context["demand"]["id"] == str(row.id)
    capacity = invoke("inspect_capacity", b, db, demand_id=row.id)
    assert people["E04"].display_name in json.dumps(capacity, ensure_ascii=False)
    simulated = invoke("simulate_plan", b, db, demand_id=row.id)
    assert simulated["case_id"]
    assert simulated["options"][0]["verdict"] in (
        "可行",
        "数据不足",
        "不可行",
        "有条件可行",
    )
    compared = invoke(
        "compare_plan_options",
        b,
        db,
        case_id=UUID(simulated["case_id"]),
        option_ids=[UUID(simulated["options"][0]["id"])],
    )
    assert compared["options"][0]["id"] == simulated["options"][0]["id"]
    assert business_rows(db, row.org_id) == before
    with pytest.raises(HTTPException):
        invoke(
            "compare_plan_options",
            a,
            db,
            case_id=UUID(simulated["case_id"]),
            option_ids=[UUID(simulated["options"][0]["id"])],
        )
    setting = db.exec(
        select(CapacitySetting).where(CapacitySetting.org_id == row.org_id)
    ).one()
    setting.enabled = False
    db.commit()
    with pytest.raises(HTTPException):
        invoke("inspect_capacity", b, db, demand_id=row.id)


def test_a_side_hides_historical_reply_prose(coordination: tuple, db: Session) -> None:
    people, _, row = coordination
    a = principal(db, row.org_id, people["E01"].id)
    b = principal(db, row.org_id, people["E05"].id)
    db.add(
        CapacityDemandEvent(
            org_id=row.org_id,
            demand_id=row.id,
            kind=DemandEventKind.REPLIED,
            actor_user_id=b.user_id,
            via="web",
            text=f"历史答复提到{people['E04'].display_name}",
        )
    )
    db.commit()
    a_result = invoke("get_approval_status", a, db, demand_id=row.id)
    assert_no_b_people(a_result, people)
    assert a_result["events"][0]["kind"] == "已答复"
    b_result = invoke("get_approval_status", b, db, demand_id=row.id)
    assert people["E04"].display_name in b_result["events"][0]["text"]
