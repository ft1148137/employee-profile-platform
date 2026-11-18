"""Borrowing through the same proposal entry points as other planning options."""

from datetime import timedelta
from functools import partial
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlmodel import Session, select

from app.core.security import create_access_token
from app.domains.audit.models import AuditEvent
from app.domains.base import utc_now
from app.domains.bot import schemas, store, tools
from app.domains.capacity.calendar import loads
from app.domains.capacity.demand import validate_load_words
from app.domains.capacity.models import (
    CapacityApproval,
    CapacityAssignment,
    CapacityDemand,
    CapacityDemandEvent,
    CapacityProposalLine,
    CapacityWeek,
)
from app.domains.change_request.models import ProfileChangeRequest
from app.domains.evidence.models import Evidence
from app.domains.org.models import Department, Employee, OrgMembership, RoleGrant
from app.domains.planning import snapshot
from app.domains.profile.models import ProfileClaim, ProfileVersion
from app.domains.review.routing import review_assignment
from app.domains.work.models import WorkEvent
from app.models import User
from tests.api.routes.test_m6_capacity_mcp import listing
from tests.api.routes.test_m9_proposal import case as case
from tests.api.routes.test_m9_proposal import planning_setup as planning_setup
from tests.api.routes.test_m9_proposal import proposal_row, propose, respond, rows
from tests.api.routes.test_m9_proposal_concurrency import race
from tests.api.routes.test_m9_proposal_entries import click
from tests.api.routes.test_mcp import call, initialize, token_for
from tests.domains.test_bot_core import enable
from tests.domains.test_im_gateway import principal


@pytest.fixture
def borrow_option(
    case: tuple, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict, dict, str]:
    url, _, auth, demand = case
    # Isolate this contract from the demo tenant's published capability evidence.
    monkeypatch.setattr(
        snapshot,
        "department_reference",
        lambda *_: SimpleNamespace(department_name="合成借出部门", count=1),
    )
    response = client.post(
        f"{url}/planning/demands/{demand.id}/cases", headers=auth["b"]
    )
    assert response.status_code == 200, response.text
    plan = response.json()
    option = next(option for option in plan["options"] if option["kind"] == "borrow")
    department_id = option["params"]["departments"][0]["department_id"]
    return plan, option, department_id


def test_borrow_proposal_starts_with_an_open_line(
    case: tuple, borrow_option: tuple, client: TestClient, db: Session
) -> None:
    _, option, department_id = borrow_option
    response = propose(
        client,
        case,
        option,
        lender_department_id=department_id,
        borrow_headcount=1,
        outside_basis=False,
    )
    assert response.status_code == 200, response.text
    assert response.json()["proposal"]["status"] == "pending"
    assert response.json()["proposal"]["headcount"] == 1
    (line,) = rows(db, CapacityProposalLine, case[3])
    assert line.employee_id is None
    assert str(line.lender_department_id) == department_id
    assert line.allocation_percent == case[3].allocation_percent


def test_borrow_confirmation_card_has_department_conditions(
    case: tuple, borrow_option: tuple, db: Session, client: TestClient
) -> None:
    _, employees, _, demand = case
    plan, option, department_id = borrow_option
    manager = principal(db, demand.org_id, employees["E05"].id)
    session = store.create_session(db, manager, channel="web")
    cards: list[schemas.Message] = []
    result = tools.call(
        db,
        manager,
        session,
        cards,
        "prepare_plan_proposal",
        {
            "case_id": plan["id"],
            "option_id": option["id"],
            "text": "合成部门条件",
            "lender_department_id": department_id,
            "borrow_headcount": 1,
        },
    )
    assert isinstance(result, dict) and result.get("status") == "pending", result
    (card,) = cards
    assert isinstance(card, schemas.CapacityCard)
    for text in (*option["summary"]["conditions"], card.content):
        validate_load_words(manager, db, demand, text)
        assert "原部门经理同意" in text
        assert "本人同意" in text
        assert "余量" not in text
        assert "M9" not in text
    assert rows(db, CapacityProposalLine, demand) == []
    enable(db, demand.org_id)
    store.append_message(
        db,
        session,
        "assistant",
        schemas.TurnResponse(messages=cards).model_dump(mode="json"),
    )
    db.commit()
    confirmed = click(db, manager, session, card, client)
    assert confirmed.status == "confirmed", confirmed
    (line,) = rows(db, CapacityProposalLine, demand)
    assert line.employee_id is None and str(line.lender_department_id) == department_id


@pytest.fixture
def offered(
    case: tuple, client: TestClient, db: Session, request: pytest.FixtureRequest
) -> tuple:
    url, people, auth, demand = case
    headcount = getattr(request, "param", 1)
    demand.headcount = headcount
    department = Department(org_id=demand.org_id, name="合成借出部门 C4")
    db.add(department)
    db.flush()
    for role in ("lender", "worker"):
        person = Employee(
            org_id=demand.org_id, department_id=department.id, display_name=f"C4 {role}"
        )
        user = User(username=f"c4-{uuid4()}", hashed_password="synthetic")
        db.add_all([person, user])
        db.flush()
        membership = OrgMembership(
            org_id=demand.org_id, employee_id=person.id, user_id=user.id
        )
        db.add(membership)
        db.flush()
        token = create_access_token(user.id, expires_delta=timedelta(hours=1))
        auth[role] = {"Authorization": f"Bearer {token}"}
        if role == "lender":
            db.add(
                RoleGrant(
                    org_id=demand.org_id,
                    membership_id=membership.id,
                    department_id=department.id,
                    role="dept_manager",
                )
            )
            lender_id = user.id
    worker = person
    dictionary = client.get(f"{url}/capabilities", headers=auth["admin"]).json()
    skill = next(
        item for item in dictionary["data"] if item["key"] == demand.capability_key
    )
    db.add(
        ProfileVersion(
            org_id=demand.org_id,
            subject_employee_id=worker.id,
            revision=99,
            schema_version_id=dictionary["schema_version"]["id"],
            published_at=utc_now(),
            published_by={"actor_type": "system_worker"},
            snapshot={
                "claims": [
                    {
                        "id": str(uuid4()),
                        "capability_definition_id": skill["id"],
                        "statement": "合成能力",
                        "level": "routine",
                        "evidence": [],
                        "assessment_status": "confirmed",
                        "confidence": None,
                        "explanation": None,
                        "generated_by": {"actor_type": "user"},
                    }
                ]
            },
        )
    )
    for offset in (0, 7):
        db.add(
            CapacityWeek(
                org_id=demand.org_id,
                employee_id=worker.id,
                week_start=demand.window_start + timedelta(days=offset),
                other_percent=10,
                confirmed_by_user_id=lender_id,
            )
        )
    db.commit()
    response = client.post(
        f"{url}/planning/demands/{demand.id}/cases", headers=auth["b"]
    )
    assert response.status_code == 200, response.text
    option = next(
        option for option in response.json()["options"] if option["kind"] == "borrow"
    )
    result = propose(
        client,
        case,
        option,
        lender_department_id=str(department.id),
        borrow_headcount=headcount,
        outside_basis=False,
    )
    assert result.status_code == 200, result.text
    lines = rows(db, CapacityProposalLine, demand)
    assert len(lines) == headcount
    return result.json(), lines[0], worker


def decide(
    client: TestClient, case: tuple, line: CapacityProposalLine, role: str, **body: str
) -> Response:
    url, _, auth, _ = case
    return client.post(
        f"{url}/capacity/borrow-requests/{line.id}/decision",
        headers=auth[role],
        json=body,
    )


@pytest.mark.parametrize("order", ["ASW", "SAW", "SWA", "S(AW)"])
def test_borrow_consent_order_and_projections(
    case: tuple, offered: tuple, client: TestClient, db: Session, order: str
) -> None:
    url, _, auth, demand = case
    reply, line, worker = offered
    employee_id = str(worker.id)
    department_id = worker.department_id
    reviewers = review_assignment(db, demand.org_id, worker.id)
    path = f"{url}/capacity/demands/{demand.id}"
    select_worker = partial(
        decide, client, case, line, "lender", action="select", employee_id=employee_id
    )
    consent = partial(decide, client, case, line, "worker", action="accept")
    accept = partial(respond, client, case, reply["revision"])
    listing = client.get(f"{url}/capacity/borrow-requests", headers=auth["lender"])
    assert listing.status_code == 200
    assert employee_id in listing.text
    assert (
        decide(
            client, case, line, "b", action="select", employee_id=employee_id
        ).status_code
        == 404
    )
    assert (
        decide(
            client, case, line, "foreign", action="select", employee_id=employee_id
        ).status_code
        == 404
    )

    def held() -> int:
        db.expire_all()
        return loads(db, demand.org_id, [worker.id], demand.window_start, 1)[worker.id][
            0
        ].hold_percent

    assert held() == 0
    for action in order.replace("(AW)", "R"):
        responses = {
            "S": lambda: [select_worker()],
            "A": lambda: [accept()],
            "W": lambda: [consent()],
            "R": lambda: race(accept, consent),
        }[action]()
        assert all(response.status_code in (200, 204) for response in responses), [
            r.text for r in responses
        ]
        if action == "S":
            assert held() == 50
            assert not rows(db, CapacityAssignment, demand)
            for side in ("a", "b"):
                text = client.get(path, headers=auth[side]).text
                assert employee_id not in text and worker.display_name not in text
    (assignment,) = rows(db, CapacityAssignment, demand)
    assert assignment.proposal_line_id == line.id and held() == 0
    accepted = [
        event
        for event in rows(db, AuditEvent, demand)
        if event.action == "capacity.demand_accepted"
    ]
    assert [event.actor_id for event in accepted] == [str(demand.requester_user_id)]

    assert worker.department_id == department_id
    assert review_assignment(db, demand.org_id, worker.id) == reviewers
    b = client.get(f"{path}/assignments", headers=auth["b"])
    assert b.status_code == 200
    assert b.json()["data"] == [
        {
            "headcount": 1,
            "allocation_percent": 50,
            "start_date": demand.window_start.isoformat(),
            "due_date": demand.window_end.isoformat(),
            "status": "active",
        }
    ]
    assert client.get(f"{path}/assignments", headers=auth["a"]).status_code == 404
    assert employee_id not in client.get(path, headers=auth["a"]).text
    lender = client.get(f"{url}/capacity/workloads", headers=auth["lender"])
    person = next(
        row for row in lender.json()["data"] if row["employee_id"] == employee_id
    )
    assert person["weeks"][0]["platform_percent"] == 50
    mine = client.get(f"{url}/capacity/assignments/me", headers=auth["worker"])
    assert mine.json()["data"][0]["employee_id"] == employee_id
    assert (
        client.get(
            f"{url}/employees/{employee_id}/profile", headers=auth["b"]
        ).status_code
        == 404
    )
    assert (
        employee_id
        not in client.get(f"{url}/capacity/workloads", headers=auth["b"]).text
    )
    assert (
        client.get(
            f"{url}/capacity/workloads",
            params={"department_id": str(department_id)},
            headers=auth["b"],
        ).status_code
        == 404
    )
    mine_for_b = client.get(f"{url}/capacity/assignments/me", headers=auth["b"])
    assert mine_for_b.json()["data"] == []


@pytest.mark.parametrize("role", ["lender", "worker"])
def test_borrow_party_cannot_also_reject_as_requesting_manager(
    case: tuple, offered: tuple, client: TestClient, db: Session, role: str
) -> None:
    url, _, auth, demand = case
    reply, line, worker = offered
    lender = db.exec(
        select(OrgMembership)
        .join(RoleGrant, RoleGrant.membership_id == OrgMembership.id)
        .where(
            OrgMembership.org_id == demand.org_id,
            RoleGrant.org_id == demand.org_id,
            RoleGrant.department_id == worker.department_id,
        )
    ).one()
    member = (
        lender
        if role == "lender"
        else db.exec(
            select(OrgMembership).where(
                OrgMembership.org_id == demand.org_id,
                OrgMembership.employee_id == worker.id,
            )
        ).one()
    )
    db.add(
        RoleGrant(
            org_id=demand.org_id,
            membership_id=member.id,
            department_id=demand.requesting_department_id,
            role="dept_manager",
        )
    )
    db.commit()
    selected = decide(
        client, case, line, "lender", action="select", employee_id=str(worker.id)
    )
    assert selected.status_code == 204, selected.text
    if role == "worker":
        consent = decide(client, case, line, "worker", action="accept")
        assert consent.status_code == 204, consent.text

    def state() -> tuple:
        db.expire_all()
        return (
            demand.revision,
            proposal_row(db, demand).model_dump(),
            {a.id: a.model_dump() for a in rows(db, CapacityApproval, demand)},
            {e.id: e.model_dump() for e in rows(db, CapacityDemandEvent, demand)},
            loads(db, demand.org_id, [worker.id], demand.window_start, 1)[worker.id][
                0
            ].hold_percent,
        )

    before = state()
    assert before[-1] == 50
    path = f"{url}/capacity/demands/{demand.id}"
    assert client.get(path, headers=auth[role]).status_code == 200
    rejected = respond(
        client, case, reply["revision"], "follow_up", role=role, text="合成拒绝理由"
    )
    assert rejected.status_code == 403 and "不能审批自己" in rejected.text
    assert state() == before
    assert not rows(db, CapacityAssignment, demand)

    independent = respond(
        client, case, reply["revision"], "follow_up", text="合成独立拒绝"
    )
    assert independent.status_code == 200, independent.text
    assert independent.json()["status"] == "submitted"
    assert proposal_row(db, demand).fail_reason == "rejected"
    assert state()[-1] == 0
    (decision,) = [
        a for a in rows(db, CapacityApproval, demand) if a.party == "requester"
    ]
    assert (
        decision.decision == "rejected" and decision.user_id == demand.requester_user_id
    )


def test_mcp_keeps_existing_reply_contract(
    case: tuple, client: TestClient, db: Session
) -> None:
    _, people, _, demand = case
    headers = initialize(client, token_for(db, people["E05"])[0])
    fields = listing(client, headers)["reply_demand"]["inputSchema"]["properties"]
    borrow = {"lender_department_id": str(uuid4()), "borrow_headcount": 1}
    assert not fields.keys() & borrow.keys()
    body = {
        "demand_id": str(demand.id),
        "revision": demand.revision,
        "outcome": "can_do",
        "start_date": demand.window_start.isoformat(),
        "end_date": demand.window_end.isoformat(),
        "text": "合成普通答复",
    }
    assert call(client, headers, "reply_demand", {**body, **borrow}).get("isError")
    result = call(client, headers, "reply_demand", body)
    assert not result.get("isError"), result
    assert result["structuredContent"]["status"] == "replied"


def test_decline_is_private_and_leaves_profiles_unchanged(
    case: tuple, offered: tuple, client: TestClient, db: Session
) -> None:
    url, _, auth, demand = case
    _, line, worker = offered
    assert (
        decide(
            client, case, line, "lender", action="select", employee_id=str(worker.id)
        ).status_code
        == 204
    )
    paths = (
        "coverage",
        "profile-export?format=json",
        f"people-search?capability_key={demand.capability_key}",
    )

    def profile_surfaces() -> list[object]:
        result = []
        for path in paths:
            response = client.get(f"{url}/{path}", headers=auth["admin"])
            assert response.status_code == 200, response.text
            result.append(response.json())
        return result

    models = (Evidence, WorkEvent, ProfileClaim, ProfileVersion, ProfileChangeRequest)
    before = [len(rows(db, model, demand)) for model in models]
    surfaces = profile_surfaces()
    private_reason = "只有本人应该看到的合成理由"
    assert (
        decide(
            client, case, line, "worker", action="decline", reason=private_reason
        ).status_code
        == 204
    )
    db.expire_all()
    assert proposal_row(db, demand).fail_reason == "borrow_declined"
    assert demand.status == "submitted"
    assert not rows(db, CapacityAssignment, demand)
    assert (
        loads(db, demand.org_id, [worker.id], demand.window_start, 1)[worker.id][
            0
        ].hold_percent
        == 0
    )
    assert before == [len(rows(db, model, demand)) for model in models]
    assert surfaces == profile_surfaces()
    for role in ("a", "b", "lender", "admin"):
        path = (
            f"capacity/demands/{demand.id}"
            if role in ("a", "b", "admin")
            else "capacity/borrow-requests"
        )
        response = client.get(f"{url}/{path}", headers=auth[role])
        assert response.status_code == 200
        assert "借调未达成" in response.text or "failed" in response.text
        assert all(
            secret not in response.text
            for secret in (private_reason, str(worker.id), worker.display_name)
        )
    mine = client.get(
        f"{url}/capacity/borrow-requests", params={"mine": True}, headers=auth["worker"]
    )
    assert mine.json()[0]["reason"] == private_reason
    audit = next(
        event
        for event in rows(db, AuditEvent, demand)
        if event.action == "capacity.demand_follow_up"
    )
    assert audit.actor_type == "system_worker" and private_reason not in str(
        audit.details
    )


@pytest.mark.parametrize(
    "failure",
    ["manager_rejects", "expired", "inactive", "transferred", "role_lost", "no_reason"],
)
def test_borrow_failure_releases_holds(
    case: tuple, offered: tuple, client: TestClient, db: Session, failure: str
) -> None:
    _, _, _, demand = case
    reply, line, worker = offered
    if failure == "manager_rejects":
        assert decide(client, case, line, "lender", action="reject").status_code == 409
        assert (
            decide(
                client, case, line, "lender", action="reject", reason="业务原因"
            ).status_code
            == 204
        )
    else:
        assert (
            decide(
                client,
                case,
                line,
                "lender",
                action="select",
                employee_id=str(worker.id),
            ).status_code
            == 204
        )
        if failure == "no_reason":
            assert (
                decide(client, case, line, "worker", action="decline").status_code
                == 204
            )
        else:
            assert (
                decide(client, case, line, "worker", action="accept").status_code == 204
            )
            if failure == "expired":
                pending = proposal_row(db, demand)
                pending.hold_expires_at = utc_now() - timedelta(seconds=1)
                db.add(pending)
            elif failure in ("inactive", "transferred"):
                if failure == "inactive":
                    worker.employment_status = "inactive"
                else:
                    worker.department_id = demand.requesting_department_id
                db.add(worker)
            else:
                grant = db.exec(
                    select(RoleGrant).where(
                        RoleGrant.org_id == demand.org_id,
                        RoleGrant.department_id == worker.department_id,
                    )
                ).one()
                db.delete(grant)
            db.commit()
            assert respond(client, case, reply["revision"]).status_code == 409
    db.expire_all()
    assert demand.status == "submitted" and not rows(db, CapacityAssignment, demand)
    assert (
        loads(db, demand.org_id, [worker.id], demand.window_start, 1)[worker.id][
            0
        ].hold_percent
        == 0
    )


def test_two_borrows_cannot_overbook_worker(
    case: tuple, offered: tuple, client: TestClient, db: Session
) -> None:
    url, _, auth, demand = case
    _, line, worker = offered
    other = CapacityDemand(**demand.model_dump(exclude={"id", "created_at"}))
    other.status, other.revision = "submitted", 1
    other.committed_start_date = other.committed_end_date = None
    db.add(other)
    db.commit()
    result = client.post(f"{url}/planning/demands/{other.id}/cases", headers=auth["b"])
    assert result.status_code == 200, result.text
    option = next(o for o in result.json()["options"] if o["kind"] == "borrow")
    result = propose(
        client,
        (*case[:3], other),
        option,
        lender_department_id=str(worker.department_id),
        borrow_headcount=1,
    )
    assert result.status_code == 200, result.text
    other_line = next(
        p for p in rows(db, CapacityProposalLine, demand) if p.id != line.id
    )
    actions = [
        partial(
            decide,
            client,
            case,
            item,
            "lender",
            action="select",
            employee_id=str(worker.id),
        )
        for item in (line, other_line)
    ]
    results = race(*actions)
    assert sorted(result.status_code for result in results) == [204, 409]


def test_borrow_extension_is_reviewed_anonymously_by_b(
    case: tuple, offered: tuple, client: TestClient, db: Session
) -> None:
    url, _, auth, demand = case
    reply, line, worker = offered
    assert (
        decide(
            client, case, line, "lender", action="select", employee_id=str(worker.id)
        ).status_code
        == 204
    )
    assert decide(client, case, line, "worker", action="accept").status_code == 204
    assert respond(client, case, reply["revision"]).status_code == 200
    (assignment,) = rows(db, CapacityAssignment, demand)
    due = assignment.due_date + timedelta(days=1)
    request = client.post(
        f"{url}/capacity/assignments/{assignment.id}/extension-requests",
        headers=auth["worker"],
        json={"requested_due_date": due.isoformat(), "reason": worker.display_name},
    )
    assert request.status_code == 200, request.text
    collection = f"{url}/capacity/extension-requests"
    listing = client.get(collection, headers=auth["b"])
    assert listing.json()["data"][0]["employee_name"] == "借调 1 人"
    assert worker.display_name not in listing.text
    path = f"{collection}/{request.json()['id']}/decision"
    body = {
        "approve": True,
        "new_committed_end_date": due.isoformat(),
        "commitment_note": "调整交付日期",
    }
    assert client.post(path, headers=auth["lender"], json=body).status_code == 404
    assert client.post(path, headers=auth["a"], json=body).status_code == 404
    unavailable = client.post(path, headers=auth["b"], json=body)
    assert unavailable.status_code == 409
    assert "weeks" not in unavailable.text and "over_percent" not in unavailable.text
    db.add(
        CapacityWeek(
            org_id=demand.org_id,
            employee_id=worker.id,
            week_start=due,
            other_percent=0,
            confirmed_by_user_id=demand.requester_user_id,
        )
    )
    db.commit()
    approved = client.post(path, headers=auth["b"], json=body)
    assert approved.status_code == 200, approved.text
    assert approved.json()["employee_name"] == "借调 1 人"
    db.refresh(assignment)
    assert assignment.due_date == due


@pytest.mark.parametrize("offered", [2], indirect=True)
def test_each_borrow_line_requires_a_different_person(
    case: tuple, offered: tuple, client: TestClient, db: Session
) -> None:
    _, _, _, demand = case
    reply, first, worker = offered
    second = next(
        line for line in rows(db, CapacityProposalLine, demand) if line.id != first.id
    )
    select_worker = {"action": "select", "employee_id": str(worker.id)}
    assert decide(client, case, first, "lender", **select_worker).status_code == 204
    duplicate = decide(client, case, second, "lender", **select_worker)
    assert duplicate.status_code == 409 and "这名员工已在本次借调中" in duplicate.text
    assert decide(client, case, first, "worker", action="accept").status_code == 204
    waiting = respond(client, case, reply["revision"])
    assert waiting.json()["proposal"]["status"] == "pending"
    assert not rows(db, CapacityAssignment, demand)
    assert (
        decide(
            client, case, second, "lender", action="reject", reason="当前无法安排第二人"
        ).status_code
        == 204
    )
    assert (
        loads(db, demand.org_id, [worker.id], demand.window_start, 1)[worker.id][
            0
        ].hold_percent
        == 0
    )
