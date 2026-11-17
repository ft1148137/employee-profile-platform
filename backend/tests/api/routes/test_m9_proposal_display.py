"""C2 display contract regressions using C1's isolated synthetic tenant."""

from datetime import timedelta
from typing import Any
from uuid import UUID

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.domains.base import utc_now
from app.domains.capacity.calendar import WeekLoad, loads
from app.domains.capacity.models import CapacityDemand, CapacityProposal
from app.domains.capacity.proposal import current_principal
from app.domains.org.models import OrgMembership
from app.domains.planning.snapshot import build, current_fingerprint
from tests.api.routes.test_m9_proposal import (
    case as case,
)
from tests.api.routes.test_m9_proposal import (
    plan,
    propose,
    segments,
)
from tests.api.routes.test_m9_proposal import (
    planning_setup as planning_setup,
)


def test_requester_can_read_each_anonymous_segment(
    case: tuple, client: TestClient
) -> None:
    url, people, auth, demand = case
    lines = segments(case, split=True)
    reply = propose(client, case, plan(client, case, lines))
    assert reply.status_code == 200, reply.text
    response = client.get(f"{url}/capacity/demands/{demand.id}", headers=auth["a"])
    assert response.status_code == 200, response.text
    summary = response.json()["proposal"]
    assert summary["headcount"] == 1
    assert summary["allocation_percents"] == [30, 50]
    assert summary["participants"] == [
        {
            "number": 1,
            "segments": [
                {k: line[k] for k in ("start_date", "end_date", "allocation_percent")}
                for line in lines
            ],
        }
    ]
    assert (
        client.get(f"{url}/capacity/demands/{demand.id}", headers=auth["a"]).json()[
            "proposal"
        ]["participants"]
        == summary["participants"]
    )
    for line in reply.json()["proposal"]["lines"]:
        assert line["id"] not in response.text
    assert people["E04"].display_name not in response.text
    assert str(people["E04"].id) not in response.text
    assert "employee_id" not in response.text
    for line in lines:
        for field in ("start_date", "end_date"):
            assert line[field] in response.text, (
                f"Requester cannot display the segment's {field}: {line[field]}"
            )


def test_team_can_read_hold_expiry(case: tuple, client: TestClient) -> None:
    url, people, auth, _ = case
    reply = propose(client, case)
    assert reply.status_code == 200, reply.text
    response = client.get(f"{url}/capacity/workloads", headers=auth["b"])
    assert response.status_code == 200, response.text
    person = next(
        row
        for row in response.json()["data"]
        if row["employee_id"] == str(people["E04"].id)
    )
    assert person["weeks"][0]["hold_percent"] == 50
    assert person["weeks"][0]["platform_percent"] == 50
    assert reply.json()["proposal"]["hold_expires_at"] in response.text, (
        "Team workload cannot display the hold's confirmation deadline"
    )


def test_anonymous_people_have_separate_stable_numbers(
    case: tuple, client: TestClient
) -> None:
    url, people, auth, demand = case
    lines = segments(case, people=2)
    lines[1]["allocation_percent"] = 40
    lines[1]["start_date"] = lines[0]["start_date"]
    reply = propose(client, case, plan(client, case, lines))
    assert reply.status_code == 200, reply.text
    response = client.get(f"{url}/capacity/demands/{demand.id}", headers=auth["a"])
    result = response.json()["proposal"]
    assert result["headcount"] == 2
    assert result["participants"] == [
        {
            "number": index,
            "segments": [
                {k: line[k] for k in ("start_date", "end_date", "allocation_percent")}
            ],
        }
        for index, line in enumerate(lines, start=1)
    ]
    assert (
        client.get(f"{url}/capacity/demands/{demand.id}", headers=auth["a"]).json()[
            "proposal"
        ]["participants"]
        == result["participants"]
    )
    for code in ("E04", "E05"):
        assert str(people[code].id) not in response.text
        assert people[code].display_name not in response.text
    assert set(result["participants"][0]) == {"number", "segments"}
    for role in ("employee", "foreign"):
        assert (
            client.get(
                f"{url}/capacity/demands/{demand.id}", headers=auth[role]
            ).status_code
            == 404
        )
    workload = client.get(f"{url}/capacity/workloads", headers=auth["a"])
    assert workload.status_code == 200
    for code in ("E04", "E05"):
        assert str(people[code].id) not in workload.text


def test_weekly_hold_details_expire_and_exclude_together(
    case: tuple, client: TestClient, db: Session
) -> None:
    url, people, auth, demand = case
    first = propose(client, case, plan(client, case, segments(case, percent=30)))
    assert first.status_code == 200, first.text
    created = client.post(
        f"{url}/capacity/demands",
        headers=auth["a"],
        json={
            k: getattr(demand, k).isoformat()
            if k.startswith("window_")
            else str(getattr(demand, k))
            if k.endswith("_id")
            else getattr(demand, k)
            for k in (
                "requesting_department_id",
                "target_department_id",
                "capability_key",
                "headcount",
                "allocation_percent",
                "window_start",
                "window_end",
                "description",
            )
        },
    )
    assert created.status_code == 200, created.text
    other = db.get(CapacityDemand, UUID(created.json()["id"]))
    assert other is not None
    second_case = (url, people, auth, other)
    second = propose(
        client,
        second_case,
        plan(client, second_case, segments(second_case, percent=40)),
    )
    assert second.status_code == 200, second.text
    rows = [
        db.get(CapacityProposal, UUID(r.json()["proposal"]["id"]))
        for r in (first, second)
    ]
    now = utc_now()
    for i, row in enumerate(rows):
        assert row is not None
        row.hold_expires_at = now + timedelta(hours=i + 1)
    db.commit()
    eid = people["E04"].id

    def week(**kwargs: Any) -> WeekLoad:
        return loads(db, demand.org_id, [eid], demand.window_start, 1, **kwargs)[eid][0]

    actual = week()
    assert actual.hold_percent == actual.platform_percent == 70
    assert sorted((h.allocation_percent, h.expires_at) for h in actual.holds) == [
        (30, rows[0].hold_expires_at),
        (40, rows[1].hold_expires_at),
    ]
    assert sum(h.allocation_percent for h in actual.holds) == actual.hold_percent
    assert all(
        set(h.model_dump()) == {"allocation_percent", "expires_at"}
        for h in actual.holds
    )
    for key in ("exclude_proposal_id", "exclude_demand_id"):
        excluded = week(
            **{key: rows[0].id if key == "exclude_proposal_id" else demand.id}
        )
        assert excluded.hold_percent == 40
        assert len(excluded.holds) == 1
    rows[0].hold_expires_at = now - timedelta(seconds=1)
    db.commit()
    assert week().hold_percent == 40 and len(week().holds) == 1
    rows[1].hold_expires_at = now - timedelta(seconds=1)
    db.commit()
    assert week().hold_percent == 0 and week().holds == []
    assert week().platform_percent == 0


def test_planning_consumes_hold_details_without_changing_fingerprint(
    case: tuple, client: TestClient, db: Session
) -> None:
    url, people, auth, demand = case
    reply = propose(client, case)
    assert reply.status_code == 200, reply.text
    # A new demand sees the other demand's holds in its Planning snapshot.
    db.refresh(demand)
    other = CapacityDemand(**demand.model_dump(exclude={"id", "created_at"}))
    other.description = "合成读取投影回归"
    db.add(other)
    db.commit()
    option = plan(client, (url, people, auth, other), segments(case, percent=30))
    assert option["verdict"] in ("feasible", "conditional")
    membership = db.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == demand.org_id,
            OrgMembership.employee_id == people["E05"].id,
        )
    ).one()
    principal = current_principal(db, demand.org_id, membership.user_id)
    before = current_fingerprint(db, principal, other)
    data, fingerprint, _ = build(db, principal, other)
    candidate = next(
        c for c in data["candidates"] if c["employee_id"] == str(people["E04"].id)
    )
    assert candidate["weeks"][0]["hold_percent"] == 50
    assert candidate["weeks"][0]["holds"] == [
        {
            "allocation_percent": 50,
            "expires_at": reply.json()["proposal"]["hold_expires_at"],
        }
    ]
    assert fingerprint == before == current_fingerprint(db, principal, other)
