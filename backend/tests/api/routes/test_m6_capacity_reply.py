import json
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import delete
from sqlmodel import Session, select

from app.authz.deps import get_org_principal
from app.authz.principal import Principal
from app.core.security import create_access_token
from app.domains.audit.models import AuditEvent
from app.domains.base import utc_now
from app.domains.capacity import demand as service
from app.domains.capacity.calendar import this_week
from app.domains.capacity.models import (
    CapacityDemandEvent,
    CapacitySetting,
    CapacityWeek,
)
from app.domains.capacity.tools import CAPACITY_TOOLS
from app.domains.inbox.models import InboxItem
from app.domains.org.models import Department, OrgMembership, RoleGrant
from app.domains.profile.models import ProfileVersion
from app.models import User

REPLY = {
    "outcome": "can_do",
    "start_date": "2026-12-01",
    "end_date": "2026-12-31",
    "text": "可以承接合成需求",
    "revision": 1,
}


@pytest.fixture
def case(demo: tuple, db: Session, client: TestClient) -> Generator[tuple]:
    url, employees, auth = demo
    org_id = employees["E01"].org_id
    members = {
        m.employee_id: m
        for m in db.exec(
            select(OrgMembership).where(OrgMembership.org_id == org_id)
        ).all()
    }
    target = employees["E04"].department_id
    child = Department(org_id=org_id, name="合成下级", parent_department_id=target)
    db.add(child)
    db.flush()
    dictionary = client.get(f"{url}/capabilities", headers=auth["admin"]).json()
    capability = dictionary["data"][0]
    for i, code in enumerate(("E04", "E05", "E06", "E07", "E08", "E09")):
        person = employees[code]
        person.department_id = child.id if i % 2 else target
        person.employment_status = "inactive" if code == "E09" else "active"
        db.add(person)
        claim = {
            "id": str(uuid4()),
            "capability_definition_id": capability["id"],
            "statement": "PRIVATE_PROFILE",
            "level": "routine",
            "assessment_status": "confirmed",
            "confidence": None,
            "explanation": None,
            "generated_by": {"actor_type": "user"},
            "evidence": [],
        }
        db.add(
            ProfileVersion(
                org_id=org_id,
                subject_employee_id=person.id,
                revision=99,
                published_at=utc_now(),
                schema_version_id=dictionary["schema_version"]["id"],
                published_by={"actor_type": "system_worker"},
                snapshot={"claims": [claim]},
            )
        )
        if i < 3:
            db.add(
                CapacityWeek(
                    org_id=org_id,
                    employee_id=person.id,
                    week_start=this_week(db, org_id),
                    other_percent=[79, 80, 101][i],
                    confirmed_by_user_id=members[None].user_id,
                )
            )
    db.add(
        RoleGrant(
            org_id=org_id,
            membership_id=members[employees["E05"].id].id,
            role="dept_manager",
            department_id=target,
        )
    )
    db.add(
        CapacitySetting(
            org_id=org_id, enabled=True, updated_by_user_id=members[None].user_id
        )
    )
    outsider = User(
        username=str(uuid4()), email=f"{uuid4()}@example.com", hashed_password="unused"
    )
    db.add(outsider)
    db.flush()
    member = OrgMembership(
        org_id=org_id, user_id=outsider.id, employee_id=employees["E10"].id
    )
    db.add(member)
    members[employees["E10"].id] = member
    db.commit()
    for role, code in (("b", "E05"), ("outside", "E10")):
        auth[role] = {
            "Authorization": f"Bearer {create_access_token(members[employees[code].id].user_id, expires_delta=timedelta(minutes=10))}"
        }

    def api(
        path: str = "",
        role: str = "employee",
        body: dict | None = None,
        status: int = 200,
    ) -> Any:
        response = client.request(
            "GET" if body is None else "POST",
            f"{url}/capacity/demands{path}",
            headers=auth[role],
            json=body,
        )
        assert response.status_code == status, response.text
        return response.json()

    body = {
        "requesting_department_id": str(employees["E01"].department_id),
        "target_department_id": str(target),
        "capability_key": capability["key"],
        "minimum_level": "routine",
        "headcount": 2,
        "allocation_percent": 50,
        "window_start": "2026-12-01",
        "window_end": "2026-12-31",
        "description": "合成需求",
    }
    yield api, employees, members, body
    db.rollback()
    for model in (
        CapacityDemandEvent,
        service.CapacityDemand,
        CapacityWeek,
        CapacitySetting,
    ):
        db.execute(delete(model).where(model.org_id == org_id))
    db.commit()


def test_flow_privacy_snapshot_notifications_and_profile_invariance(
    case: tuple,
    demo: tuple,
    db: Session,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api, employees, members, body = case
    url, _, auth = demo
    reads = [
        *(
            (f"{url}/employees/{employees[c].id}/profile", {})
            for c in ("E04", "E05", "E06", "E07", "E08")
        ),
        (f"{url}/people-search", {"capability_key": body["capability_key"]}),
        (f"{url}/profile-export", {"format": "json"}),
    ]

    def profiles() -> list:
        responses = [client.get(p, params=q, headers=auth["b"]) for p, q in reads]
        assert all(r.status_code == 200 for r in responses)
        return [r.json() for r in responses]

    before = profiles()
    created = api(body=body)
    path = f"/{created['id']}"
    assert api(path, "b")["basis_url"].endswith(f"{path}/basis")
    original_find = service.find_people
    callers = []

    def scoped_find(session: Session, principal: Principal, args: Any) -> Any:
        callers.append(principal.user_id)
        return original_find(session, principal, args)

    with monkeypatch.context() as patch:
        patch.setattr(service, "find_people", scoped_find)
        basis = api(path + "/basis", "b")
    assert callers == [members[employees["E05"].id].user_id, None]
    assert basis["count"] == 5
    assert {p["employee_id"] for p in basis["data"]} == {
        str(employees[c].id) for c in ("E04", "E05", "E06", "E07", "E08")
    }
    assert [p["display_name"] for p in basis["data"]] == sorted(
        p["display_name"] for p in basis["data"]
    )
    assert {p["workload"]["band"] for p in basis["data"]} == {
        "available",
        "full",
        "overloaded",
        "unconfirmed",
    }
    for p in basis["data"]:
        assert p["workload"]["jira_task_count"] is None
    # Change the load after the basis was read: reply must snapshot the new server values.
    workload = db.exec(
        select(CapacityWeek).where(
            CapacityWeek.org_id == employees["E01"].org_id,
            CapacityWeek.employee_id == employees["E04"].id,
            CapacityWeek.week_start == this_week(db, employees["E01"].org_id),
        )
    ).one()
    workload.other_percent = 120
    db.add(workload)
    db.commit()
    replied = api(
        path + "/reply",
        "b",
        {**REPLY, "basis": {"count": 999, "employee_id": str(employees["E04"].id)}},
    )
    event = replied["events"][-1]
    assert event["basis"] == api(path + "/basis", "b")["reference"]
    assert event["basis"]["available"] == 0 and event["basis"]["overloaded"] == 2
    assert event["outcome"] == "can_do" and event["start_date"] == REPLY["start_date"]
    assert event["responder_name"] == employees["E05"].display_name
    responses = [api(), api(path), api(role="manager"), api(path, "manager")]
    for response in responses:
        raw = json.dumps(response, ensure_ascii=False)
        assert "PRIVATE_PROFILE" not in raw
        for code in ("E04", "E05", "E06", "E07", "E08", "E09"):
            person = employees[code]
            for private in (
                str(person.id),
                person.employee_code,
                *([] if code == "E05" else [person.display_name]),
            ):
                assert private not in raw
        for key in (
            "workload",
            "confirmed_percent",
            "assignments",
            "employee_id",
            "employee_code",
        ):
            assert f'"{key}"' not in raw
    for role in ("employee", "manager", "outside", "foreign"):
        api(path + "/basis", role, status=404)
    api(path + "/reply", "b", REPLY, 409)
    follow_up = api(
        path + "/respond",
        "manager",
        {"action": "follow_up", "text": "请再确认时间", "revision": 2},
    )
    assert (
        follow_up["status"] == "submitted" and follow_up["committed_end_date"] is None
    )
    partial = api(path + "/reply", "b", {**REPLY, "revision": 3, "outcome": "partial"})
    accepted = api(path + "/respond", body={"action": "accept", "revision": 4})
    assert accepted["status"] == "accepted" and accepted["revision"] == 5
    assert (
        accepted["committed_end_date"]
        == partial["committed_end_date"]
        == REPLY["end_date"]
    )
    assert [e["kind"] for e in accepted["events"]] == [
        "submitted",
        "replied",
        "follow_up",
        "replied",
        "accepted",
    ]
    api(path + "/respond", body={"action": "withdraw", "revision": 5}, status=409)
    api(path + "/reply", "b", {**REPLY, "revision": 5}, 409)
    assert profiles() == before
    org_id = employees["E01"].org_id
    audit = db.exec(
        select(AuditEvent)
        .where(AuditEvent.org_id == org_id, AuditEvent.target_id == created["id"])
        .order_by(AuditEvent.created_at)
    ).all()
    assert [a.action for a in audit] == [
        f"capacity.demand_{e['kind']}" for e in accepted["events"]
    ]
    assert all(a.details == {"via": "web", "client_name": None} for a in audit)
    recipients = db.exec(
        select(InboxItem.recipient_user_id)
        .where(InboxItem.org_id == org_id, InboxItem.kind == "capacity")
        .order_by(InboxItem.created_at)
    ).all()
    assert recipients == [
        members[employees[c].id].user_id for c in ("E05", "E02", "E05", "E02", "E05")
    ]


def test_requester_reply_notification_contains_only_demand_context(
    case: tuple, demo: tuple, client: TestClient
) -> None:
    api, employees, _, body = case
    sender = employees["E04"]
    created = api(body=body)
    private_reply = "合成受限答复说明"
    api(f"/{created['id']}/reply", "b", {**REPLY, "text": private_reply})
    url, _, auth = demo
    response = client.get(f"{url}/me/inbox", headers=auth["employee"])
    assert response.status_code == 200
    [message] = [
        item["message"]
        for item in response.json()["data"]
        if item["kind"] == "capacity"
    ]
    assert message == (
        f"协作需求已收到答复（{created['capability_name']}，"
        f"{body['window_start']} 至 {body['window_end']}）。"
        "请前往「已发出的需求」查看答复并决定下一步。"
    )
    assert all(
        value not in message
        for value in (
            sender.display_name,
            sender.employee_code,
            str(sender.id),
            private_reply,
            "%",
        )
    )


def test_authorization_validation_and_terminal_states(case: tuple, db: Session) -> None:
    api, employees, members, body = case
    row = api(body=body)
    path = f"/{row['id']}"
    for role in ("employee", "manager", "outside", "foreign"):
        api(path + "/reply", role, REPLY, 404)
    for role in ("b", "outside", "foreign", "admin"):
        for action in ("accept", "follow_up", "withdraw"):
            api(
                path + "/respond",
                role,
                {"action": action, "revision": 1, "text": "合成回应"},
                404,
            )
    for suffix in ("", "/basis"):
        api(f"/{uuid4()}{suffix}", "b", status=404)
        api(path + suffix, "foreign", status=404)
    for patch in (
        {"start_date": None},
        {"end_date": "2026-11-01"},
        {"text": " "},
        {"text": "x" * 2001},
        {"revision": True},
        {"outcome": "invalid"},
    ):
        api(path + "/reply", "b", {**REPLY, **patch}, 422)
    for private in (
        *(employees[code].display_name for code in ("E04", "E05", "E08")),
        str(employees["E05"].id),
        employees["E05"].employee_code,
    ):
        api(
            path + "/reply",
            "b",
            {**REPLY, "text": f"安排{private}处理"},
            422,
        )
    api(path + "/respond", body={"action": "follow_up", "revision": 1}, status=422)
    api(path + "/respond", body={"action": "accept", "revision": 1}, status=409)
    rejection = {
        "outcome": "not_now",
        "reason_code": "capacity",
        "text": "暂时不能承接",
        "revision": 1,
        "start_date": None,
        "end_date": None,
    }
    invalid = api(path + "/reply", "b", {**rejection, "start_date": "2026-12-01"}, 422)
    assert "请同时填写开始和结束日期" in invalid["detail"][0]["msg"]
    assert api(path, "b")["status"] == "submitted"

    rejected = api(path + "/reply", "b", rejection)
    assert rejected["status"] == "replied"
    assert rejected["events"][-1]["start_date"] is None
    assert rejected["events"][-1]["end_date"] is None
    assert api(path, "b")["status"] == "replied"
    api(path + "/respond", body={"action": "accept", "revision": 2}, status=409)
    api(path + "/respond", body={"action": "withdraw", "revision": 1}, status=409)
    withdrawn = api(path + "/respond", body={"action": "withdraw", "revision": 2})
    assert withdrawn["status"] == "withdrawn"
    api(path + "/respond", body={"action": "withdraw", "revision": 3}, status=409)
    own = api(role="admin", body=body)
    api(f"/{own['id']}/reply", "admin", REPLY, 403)
    early = api(body=body)
    api(f"/{early['id']}/respond", body={"action": "withdraw", "revision": 1})
    org_id = employees["E01"].org_id
    withdrawn_audit = db.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == org_id,
            AuditEvent.target_id == withdrawn["id"],
            AuditEvent.action == "capacity.demand_withdrawn",
        )
    ).one()
    assert withdrawn_audit.details == {"via": "web", "client_name": None}


def test_tools_enforce_scope_and_record_client(case: tuple, db: Session) -> None:
    # Tools get a fresh request session, not metadata left on the suite fixture.
    with Session(db.get_bind()) as tool_db:
        api, employees, members, body = case
        tools = {t.name: t for t in CAPACITY_TOOLS}
        b = get_org_principal(
            employees["E01"].org_id,
            tool_db.get_one(User, members[employees["E05"].id].user_id),
            tool_db,
        )
        a = get_org_principal(
            b.org_id,
            tool_db.get_one(User, members[employees["E02"].id].user_id),
            tool_db,
        )
        row = api(body=body)
        for name, payload in (
            ("get_demand_basis", {}),
            ("reply_demand", REPLY),
            ("respond_demand", {"action": "accept", "revision": 2}),
        ):
            assert tools[name].writes == (name != "get_demand_basis")
            valid = {"demand_id": row["id"], **payload}
            tools[name].input_model.model_validate(valid)
            with pytest.raises(ValidationError):
                tools[name].input_model.model_validate(
                    {**valid, "org_id": str(uuid4())}
                )
        data = tools["reply_demand"].input_model.model_validate(
            {"demand_id": row["id"], **REPLY, "basis": {"count": 1234}}
        )
        with pytest.raises(service.CapacityNotFound):
            tools["get_demand_basis"].handler(a, tool_db, demand_id=UUID(row["id"]))
        result = tools["reply_demand"].handler(
            b, tool_db, **data.model_dump(), via="mcp", client_name="synthetic-client"
        )
        assert result.events[-1].basis.count == 5
        tool_db.commit()
        data = tools["respond_demand"].input_model.model_validate(
            {"demand_id": row["id"], "action": "withdraw", "revision": 2}
        )
        tools["respond_demand"].handler(
            a, tool_db, **data.model_dump(), via="mcp", client_name="synthetic-client"
        )
        tool_db.commit()
        events = tool_db.exec(
            select(AuditEvent).where(
                AuditEvent.org_id == b.org_id,
                AuditEvent.target_id == row["id"],
                AuditEvent.action != "capacity.demand_submitted",
            )
        ).all()
        assert len(events) == 2 and all(
            e.details == {"via": "mcp", "client_name": "synthetic-client"}
            for e in events
        )


def test_concurrent_reply_revision(
    case: tuple, demo: tuple, client: TestClient
) -> None:
    api, _, _, body = case
    row = api(body=body)

    def reply(_: int) -> int:
        return client.post(
            f"{demo[0]}/capacity/demands/{row['id']}/reply",
            headers=demo[2]["b"],
            json=REPLY,
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(reply, range(2))) == [200, 409]
    assert [e["kind"] for e in api(f"/{row['id']}")["events"]] == [
        "submitted",
        "replied",
    ]
