import json
from collections.abc import Generator
from datetime import date
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlmodel import Session, select

from app.domains.audit.models import AuditEvent
from app.domains.capacity.models import (
    CapacityAssignment,
    CapacityDemand,
    CapacityDemandEvent,
    CapacityExtensionRequest,
    CapacitySetting,
    CapacityWorkloadRequest,
)
from app.domains.capacity.tools import CAPACITY_TOOLS
from app.domains.evidence.models import Evidence
from app.domains.org.models import Employee, OrgMembership, RoleGrant
from app.domains.profile.models import ProfileVersion
from app.models import User
from tests.api.routes.test_mcp import call, initialize, token_for

NAMES = {t.name for t in CAPACITY_TOOLS}
REPLY = {
    "outcome": "can_do",
    "start_date": "2026-12-01",
    "end_date": "2026-12-31",
    "text": "合成部门答复正文",
    "revision": 1,
}


@pytest.fixture
def capacity(demo: tuple, db: Session, client: TestClient) -> Generator[tuple]:
    url, people, auth = demo
    org_id = people["E01"].org_id
    member = db.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == org_id,
            OrgMembership.employee_id == people["E05"].id,
        )
    ).one()
    db.add(
        RoleGrant(
            org_id=org_id,
            membership_id=member.id,
            role="dept_manager",
            department_id=people["E04"].department_id,
        )
    )
    db.add(
        CapacitySetting(org_id=org_id, enabled=True, updated_by_user_id=member.user_id)
    )
    foreign_user = UUID(
        jwt.decode(
            auth["foreign"]["Authorization"].split()[1],
            options={"verify_signature": False},
        )["sub"]
    )
    foreign = db.exec(
        select(OrgMembership).where(OrgMembership.user_id == foreign_user)
    ).one()
    foreign_person = Employee(org_id=foreign.org_id, display_name="合成外企业员工")
    db.add(foreign_person)
    db.flush()
    foreign.employee_id = foreign_person.id
    db.add(foreign)
    db.add(
        CapacitySetting(
            org_id=foreign.org_id, enabled=True, updated_by_user_id=foreign_user
        )
    )
    for code in ("E04", "E10"):
        user = User(
            username=str(uuid4()),
            email=f"{uuid4()}@example.com",
            hashed_password="unused",
        )
        db.add(user)
        db.flush()
        db.add(
            OrgMembership(org_id=org_id, user_id=user.id, employee_id=people[code].id)
        )
    db.commit()
    headers = {
        role: initialize(client, token_for(db, person)[0], name="Capacity client")
        for role, person in {
            "a": people["E02"],
            "am": people["E01"],
            "bm": people["E05"],
            "w": people["E04"],
            "outside": people["E10"],
            "foreign": foreign_person,
        }.items()
    }
    capability = client.get(f"{url}/capabilities", headers=auth["admin"]).json()[
        "data"
    ][0]
    body = {
        "requesting_department_id": str(people["E02"].department_id),
        "target_department_id": str(people["E04"].department_id),
        "capability_key": capability["key"],
        "headcount": 2,
        "allocation_percent": 50,
        "window_start": "2026-12-01",
        "window_end": "2026-12-31",
        "description": "合成需求正文",
    }
    yield people, body, headers
    db.rollback()
    for model in (
        CapacityExtensionRequest,
        CapacityAssignment,
        CapacityDemandEvent,
        CapacityDemand,
        CapacityWorkloadRequest,
        CapacitySetting,
    ):
        db.execute(delete(model).where(model.org_id.in_([org_id, foreign.org_id])))
    db.commit()


def listing(client: TestClient, headers: dict[str, str]) -> dict:
    response = client.post(
        "/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
    )
    assert response.status_code == 200, response.text
    return {t["name"]: t for t in response.json()["result"]["tools"]}


def use(client: TestClient, headers: dict[str, str], name: str, args: dict) -> dict:
    result = call(client, headers, name, args)
    assert not result.get("isError"), result
    return result["structuredContent"]


def denied(result: dict, status: int) -> None:
    assert result["isError"], result
    assert json.loads(result["content"][0]["text"])["status"] == status


def test_dynamic_listing_and_disabled_calls(
    capacity: tuple, db: Session, client: TestClient
) -> None:
    people, _, headers = capacity
    org_id = people["E01"].org_id
    enabled = listing(client, headers["a"])
    assert NAMES <= enabled.keys()
    for t in CAPACITY_TOOLS:
        description = enabled[t.name]["description"]
        assert "参考不是承诺" in description
        if t.writes:
            assert "用户明确同意" in description and "不写人名" in description
    assert (
        not {
            "assign_demand",
            "adjust_assignment",
            "decide_extension_request",
            "decide_workload_request",
        }
        & enabled.keys()
    )
    setting = db.exec(
        select(CapacitySetting).where(CapacitySetting.org_id == org_id)
    ).one()
    setting.enabled = False
    db.add(setting)
    db.commit()
    disabled = listing(client, headers["a"])
    assert not NAMES & disabled.keys()
    unknown = call(client, headers["a"], "unknown_capacity_tool", {})
    denied(unknown, 404)
    for name in NAMES:
        # Invalid arguments must not reveal a disabled tool's schema or existence.
        assert (
            call(client, headers["a"], name, {"invalid": True})["content"]
            == unknown["content"]
        )
    assert NAMES <= listing(client, headers["foreign"]).keys()
    setting.enabled = True
    setting.requester_policy = "managers"
    db.add(setting)
    db.commit()
    requester_tools = {
        "submit_demand",
        "capacity_reference",
        "list_capacity_departments",
    }
    assert NAMES & listing(client, headers["a"]).keys() == NAMES - requester_tools
    assert NAMES <= listing(client, headers["am"]).keys()
    denied(call(client, headers["a"], "submit_demand", {}), 404)
    setting.requester_policy = "everyone"
    db.add(setting)
    db.commit()
    assert listing(client, headers["a"]) == enabled


def test_flow_privacy_and_audit(
    capacity: tuple, db: Session, client: TestClient
) -> None:
    people, body, headers = capacity
    a, b = headers["a"], headers["bm"]
    before = {
        model: [
            r.model_dump(mode="json")
            for r in db.exec(
                select(model).where(model.org_id == people["E01"].org_id)
            ).all()
        ]
        for model in (Evidence, ProfileVersion)
    }
    results = [use(client, a, "list_capacity_departments", {})]
    reference = {k: body[k] for k in ("target_department_id", "capability_key")}
    results.append(use(client, a, "capacity_reference", reference))
    created = use(client, a, "submit_demand", body)
    demand_id = created["id"]
    args = {"demand_id": demand_id}
    results.append(created)
    denied(call(client, a, "get_demand_basis", args), 404)
    results.append(call(client, a, "get_demand_basis", args))
    results.append(call(client, a, "reply_demand", {**args, **REPLY}))
    basis = use(client, b, "get_demand_basis", args)
    assert "data" in basis and "reference" in basis
    use(client, b, "reply_demand", {**args, **REPLY})
    results.append(
        use(
            client,
            a,
            "respond_demand",
            {**args, "action": "follow_up", "text": "合成追问正文", "revision": 2},
        )
    )
    use(client, b, "reply_demand", {**args, **REPLY, "revision": 3})
    accepted = use(
        client, a, "respond_demand", {**args, "action": "accept", "revision": 4}
    )
    assert accepted["status"] == "accepted" and accepted["revision"] == 5
    assert [e["kind"] for e in accepted["events"]] == [
        "submitted",
        "replied",
        "follow_up",
        "replied",
        "accepted",
    ]
    results.extend(
        [
            accepted,
            use(client, a, "get_demand", args),
            use(client, a, "list_demands", {}),
        ]
    )
    db.expire_all()
    row = db.get_one(CapacityDemand, UUID(demand_id))
    assignment = CapacityAssignment(
        org_id=row.org_id,
        demand_id=row.id,
        employee_id=people["E04"].id,
        allocation_percent=50,
        due_date=date(2026, 12, 20),
        assigned_by_user_id=db.exec(
            select(OrgMembership.user_id).where(
                OrgMembership.org_id == row.org_id,
                OrgMembership.employee_id == people["E05"].id,
            )
        ).one(),
    )
    db.add(assignment)
    db.commit()
    extension = {
        "assignment_id": str(assignment.id),
        "requested_due_date": "2027-01-01",
        "reason": "合成延期正文",
    }
    results.append(call(client, a, "request_extension", extension))
    denied(results[-1], 404)
    denied(call(client, headers["foreign"], "request_extension", extension), 404)
    assert use(client, headers["foreign"], "list_my_assignments", {}) == {
        "data": [],
        "count": 0,
    }
    own = use(client, headers["w"], "list_my_assignments", {})
    assert [r["id"] for r in own["data"]] == [str(assignment.id)]
    use(client, headers["w"], "request_extension", extension)
    results.extend(
        [
            use(client, a, "list_my_assignments", {}),
            use(client, a, "get_my_workload", {}),
            use(
                client,
                a,
                "request_workload_change",
                {"requested_percent": 80, "reason": "合成负荷正文"},
            ),
        ]
    )
    for result in results:
        raw = json.dumps(result, ensure_ascii=False)
        for person in (people["E04"], people["E05"], people["E06"]):
            # The department representative's display name is explicitly public.
            private = [str(person.id), person.employee_code]
            if person.id != people["E05"].id:
                private.append(person.display_name)
            assert all(value not in raw for value in private)
    audits = db.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == row.org_id, AuditEvent.action.like("capacity.%")
        )
    ).all()
    assert {e.action for e in audits} == {
        "capacity.demand_submitted",
        "capacity.demand_replied",
        "capacity.demand_follow_up",
        "capacity.demand_accepted",
        "capacity.workload_requested",
        "capacity.extension_requested",
    }
    assert all(
        e.details["via"] == "mcp" and e.details["client_name"] == "Capacity client"
        for e in audits
    )
    raw = json.dumps(
        [
            e.model_dump(mode="json")
            for e in db.exec(
                select(AuditEvent).where(AuditEvent.org_id == row.org_id)
            ).all()
        ],
        ensure_ascii=False,
    )
    for secret in (
        body["description"],
        REPLY["text"],
        "合成追问正文",
        "合成延期正文",
        "合成负荷正文",
        a["Authorization"].split()[1],
    ):
        assert secret not in raw
    db.expire_all()
    for model, original in before.items():
        assert [
            r.model_dump(mode="json")
            for r in db.exec(select(model).where(model.org_id == row.org_id)).all()
        ] == original


def test_scope_and_strict_identity(
    capacity: tuple, db: Session, client: TestClient
) -> None:
    people, body, headers = capacity
    row = use(client, headers["a"], "submit_demand", body)
    args = {"demand_id": row["id"]}
    for role in ("a", "am", "outside", "foreign"):
        denied(call(client, headers[role], "reply_demand", {**args, **REPLY}), 404)
    for role in ("bm", "outside", "foreign"):
        denied(
            call(
                client,
                headers[role],
                "respond_demand",
                {**args, "action": "withdraw", "revision": 1},
            ),
            404,
        )
    for role in ("outside", "foreign"):
        for name in ("get_demand", "get_demand_basis"):
            denied(call(client, headers[role], name, args), 404)
        for side in ("outgoing", "incoming"):
            assert use(client, headers[role], "list_demands", {"side": side}) == {
                "data": [],
                "count": 0,
            }
    denied(
        call(
            client,
            headers["foreign"],
            "capacity_reference",
            {k: body[k] for k in ("target_department_id", "capability_key")},
        ),
        404,
    )
    denied(call(client, headers["foreign"], "submit_demand", body), 404)
    for tool, valid in (
        ("request_workload_change", {"requested_percent": 90, "reason": "合成原因"}),
        (
            "request_extension",
            {
                "assignment_id": row["id"],
                "requested_due_date": "2027-01-01",
                "reason": "合成原因",
            },
        ),
    ):
        for field in (
            "employee_id",
            "org_id",
            "user_id",
            "via",
            "client_name",
            "principal",
        ):
            result = call(
                client, headers["a"], tool, {**valid, field: str(people["E04"].id)}
            )
            assert (
                result["isError"]
                and result["content"][0]["text"] == "工具参数无效，请核对字段和格式"
            )
    assert not db.exec(
        select(CapacityWorkloadRequest).where(
            CapacityWorkloadRequest.org_id == people["E01"].org_id
        )
    ).all()
    assert not db.exec(
        select(CapacityExtensionRequest).where(
            CapacityExtensionRequest.org_id == people["E01"].org_id
        )
    ).all()
    # A reply containing an employee identifier is rejected at the shared service.
    for private in (
        people["E04"].display_name,
        str(people["E04"].id),
        people["E04"].employee_code,
    ):
        denied(
            call(
                client,
                headers["bm"],
                "reply_demand",
                {**args, **REPLY, "text": private},
            ),
            422,
        )
    assert use(client, headers["a"], "get_demand", args)["revision"] == 1
    member = db.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == people["E05"].org_id,
            OrgMembership.employee_id == people["E05"].id,
        )
    ).one()
    db.execute(
        delete(RoleGrant).where(
            RoleGrant.org_id == member.org_id, RoleGrant.membership_id == member.id
        )
    )
    db.commit()
    for name, payload in (
        ("get_demand_basis", args),
        ("reply_demand", {**args, **REPLY}),
    ):
        denied(call(client, headers["bm"], name, payload), 404)
    people["E05"].employment_status = "inactive"
    db.add(people["E05"])
    db.commit()
    for method, params in (
        ("tools/list", {}),
        ("tools/call", {"name": "reply_demand", "arguments": {**args, **REPLY}}),
    ):
        response = client.post(
            "/mcp",
            headers=headers["bm"],
            json={"jsonrpc": "2.0", "id": 4, "method": method, "params": params},
        )
        assert response.status_code == 404
