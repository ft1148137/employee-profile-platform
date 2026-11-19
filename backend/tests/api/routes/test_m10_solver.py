from collections.abc import Generator, Sequence
from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
import yaml
from fastapi.testclient import TestClient
from ortools.sat.python import cp_model
from sqlmodel import Session, delete, select

from app.domains.capacity.models import (
    CapacityApproval,
    CapacityAssignment,
    CapacityProposal,
    CapacityProposalLine,
    CapacityUnavailability,
)
from app.domains.org.models import Organization
from app.domains.planning.cpsat import SOLVE_TIMEOUT_SECONDS, CpSatSolver
from app.domains.planning.solver import Role, Solution, full_plan
from app.domains.planning.validator import PHASED_APPROVAL
from app.models import User
from scripts.eval_solver import metrics
from tests.api.routes.test_m9_change import submit_change
from tests.api.routes.test_m9_proposal import propose, respond, rows
from tests.eval.planning.test_planning_eval import HERE, _cleanup, _setup


@pytest.fixture
def replay(db: Session, request: pytest.FixtureRequest) -> Generator[tuple]:
    name = getattr(request, "param", "multi-capability-01")
    base = {
        "continuous": "unavailability-01",
        "continuity-before-capacity": "unavailability-01",
        "optional": "multi-capability-01",
        "relay": "unavailability-01",
        "mixed-relay": "unavailability-01",
    }.get(name, name)
    raw = yaml.safe_load((HERE / "cases" / f"{base}.yaml").read_text())
    if name in {"continuous", "continuity-before-capacity"}:
        raw["people"] = [
            {"code": code, "level": "routine", "other": 20}
            for code in ("QG007", "QG008")
        ]
        raw["unavailability"] = []
        if name == "continuity-before-capacity":
            raw["people"][0]["weekly_other"] = [20, 40] + [20] * 8
            raw["people"][1]["weekly_other"] = [40, 20] + [20] * 8
    elif name == "optional":
        raw["people"] = [
            person for person in raw["people"] if person["code"] in {"QG008", "QG009"}
        ]
        raw["demand"] = {"weeks": 1}
    elif name in {"relay", "mixed-relay"}:
        raw["people"].append({"code": "QG008", "level": "routine", "other": 10})
        raw["unavailability"].append({"code": "QG008", "start_day": 7, "end_day": 11})
        if name == "mixed-relay":
            raw["people"].append({"code": "QG009", "level": "routine", "other": 20})
            raw["demand"] = {"headcount": 2}
    users = set(db.exec(select(User.id)).all())
    org = Organization(slug=f"solver-api-{uuid4().hex}", name="Solver API test")
    db.add(org)
    db.flush()
    org_id = org.id
    try:
        _, people, auth, demand, _ = _setup(db, raw, org)
        yield (
            f"/api/v1/orgs/{org_id}",
            people,
            {**auth, "a": auth["a_manager"], "b": auth["b_manager"]},
            demand,
        )
    finally:
        db.rollback()
        for model in (
            CapacityAssignment,
            CapacityApproval,
            CapacityProposalLine,
            CapacityProposal,
        ):
            db.exec(delete(model).where(model.org_id == org_id))
        db.commit()
        _cleanup(db, org_id, users)


def create_case(client: TestClient, replay: tuple) -> dict[str, Any]:
    url, _, auth, demand = replay
    created = client.post(
        f"{url}/planning/demands/{demand.id}/cases", headers=auth["b"]
    )
    assert created.status_code == 200, created.text
    return created.json()


@pytest.mark.parametrize(
    ("replay", "delay_days"),
    [("multi-capability-01", 0), ("unavailability-01", 5)],
    indirect=["replay"],
)
def test_optimized_option_matches_b1_replay(
    client: TestClient, replay: tuple, delay_days: int
) -> None:
    url, _, auth, demand = replay
    assert (
        client.post(
            f"{url}/planning/demands/{demand.id}/cases", headers=auth["a"]
        ).status_code
        == 404
    )
    created = create_case(client, replay)
    case_url = f"{url}/planning/cases/{created['id']}"
    assert client.get(case_url, headers=auth["a"]).status_code == 404
    assert client.get(case_url, headers=auth["b"]).json() == created
    (option,) = [o for o in created["options"] if o["kind"] == "optimized"]
    assert option["kind_label"] == "优化方案"
    assignees = option["params"]["assignees"]
    if delay_days:
        assert option["verdict"] == "conditional"
        assert option["summary"]["conditions"] == ["需求方要接受新日期"]
        assert option["summary"]["approvers"] == [
            "本部门经理确认答复",
            "需求方接受新日期",
        ]
        assert len(assignees) == 1
    else:
        assert "需求方接受新日期" not in option["summary"]["approvers"]
        assert (PHASED_APPROVAL in option["summary"]["conditions"]) == (
            len({row["employee_id"] for row in assignees}) > 1
        )
    assert metrics(
        [Role("1", created["data"])],
        Solution({"1": option["params"]["assignees"]}, ()),
    ) == {"filled": 1, "delay": delay_days, "newcomer": 0, "violations": 0}


@pytest.mark.parametrize("replay", ["unavailability-01"], indirect=True)
def test_optimized_option_uses_m9_reply_and_accept(
    client: TestClient, db: Session, replay: tuple
) -> None:
    url, people, auth, demand = replay
    created = create_case(client, replay)
    (option,) = [o for o in created["options"] if o["kind"] == "optimized"]
    assert not rows(db, CapacityProposal, demand)
    assert not rows(db, CapacityAssignment, demand)

    start = demand.window_start + timedelta(weeks=1)
    end = demand.window_end + timedelta(weeks=1)
    phased = client.post(
        f"{url}/planning/cases/{created['id']}/options/{option['id']}/revise",
        headers=auth["b"],
        json={
            "change": "phased",
            "split_date": (start + timedelta(weeks=1)).isoformat(),
        },
    )
    assert phased.status_code == 200, phased.text
    assert len(phased.json()["params"]["assignees"]) == 2
    assert PHASED_APPROVAL in phased.json()["summary"]["conditions"]

    reply = propose(client, replay, option, outside_basis=False)
    assert reply.status_code == 200, reply.text
    assert reply.json()["proposal"]["status"] == "pending"
    (proposal,) = rows(db, CapacityProposal, demand)
    assert str(proposal.source_option_id) == option["id"]
    assert not rows(db, CapacityAssignment, demand)

    accepted = respond(client, replay, reply.json()["revision"])
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["proposal"]["status"] == "committed"
    (assignment,) = rows(db, CapacityAssignment, demand)
    assert assignment.employee_id == people["QG007"].id
    assert assignment.allocation_percent == 50
    assert (assignment.start_date, assignment.due_date) == (start, end)

    db.refresh(demand)
    changed = create_case(client, replay)
    assert changed["data"]["demand"]["change"] is True
    (change_option,) = [o for o in changed["options"] if o["kind"] == "optimized"]
    assert change_option["params"]["assignees"] == option["params"]["assignees"]
    assert change_option["summary"]["conditions"] == []
    change = submit_change(client, replay, change_option, outside_basis=False)
    assert change.status_code == 200, change.text


@pytest.mark.parametrize("replay", ["relay"], indirect=True)
def test_optimized_option_preserves_real_handoff(
    client: TestClient, replay: tuple
) -> None:
    _, people, _, demand = replay
    created = create_case(client, replay)
    (option,) = [o for o in created["options"] if o["kind"] == "optimized"]
    middle = demand.window_start + timedelta(weeks=1)
    assert option["params"]["assignees"] == [
        {
            "employee_id": str(people[code].id),
            "allocation_percent": 50,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        }
        for code, start, end in (
            ("QG008", demand.window_start, middle - timedelta(days=1)),
            ("QG007", middle, demand.window_end),
        )
    ]
    assert option["verdict"] == "conditional"
    assert option["summary"]["conditions"] == [PHASED_APPROVAL]
    assert option["summary"]["phased_origin"] is True


@pytest.mark.parametrize(
    ("replay", "expected_codes"),
    [
        ("continuous", ("QG007", "QG008")),
        ("continuity-before-capacity", ("QG007", "QG008")),
        ("optional", ("QG009",)),
    ],
    indirect=["replay"],
)
def test_optimized_option_prefers_continuity_and_optional_capability(
    client: TestClient, replay: tuple, expected_codes: tuple[str, ...]
) -> None:
    _, people, _, demand = replay
    created = create_case(client, replay)
    (option,) = [o for o in created["options"] if o["kind"] == "optimized"]
    (assignee,) = option["params"]["assignees"]
    assert assignee["employee_id"] in {str(people[code].id) for code in expected_codes}
    assert assignee["start_date"] == demand.window_start.isoformat()
    assert assignee["end_date"] == demand.window_end.isoformat()
    assert option["verdict"] == "feasible"
    assert PHASED_APPROVAL not in option["summary"]["conditions"]


@pytest.mark.parametrize("replay", ["mixed-relay"], indirect=True)
def test_optimized_option_commits_continuous_staff_once_during_handoff(
    client: TestClient, db: Session, replay: tuple
) -> None:
    _, people, _, demand = replay
    created = create_case(client, replay)
    (option,) = [o for o in created["options"] if o["kind"] == "optimized"]
    assignees = option["params"]["assignees"]
    middle = demand.window_start + timedelta(weeks=1)
    expected = {
        people["QG008"].id: (demand.window_start, middle - timedelta(days=1)),
        people["QG009"].id: (demand.window_start, demand.window_end),
        people["QG007"].id: (middle, demand.window_end),
    }
    assert len(assignees) == 3
    assert assignees == sorted(
        assignees, key=lambda row: (row["start_date"], row["employee_id"])
    )
    assert {
        row["employee_id"]: (row["start_date"], row["end_date"]) for row in assignees
    } == {
        str(employee): (start.isoformat(), end.isoformat())
        for employee, (start, end) in expected.items()
    }
    assert full_plan(created["data"], assignees)
    assert option["verdict"] == "conditional"
    assert option["summary"]["conditions"] == [PHASED_APPROVAL]
    assert option["summary"]["phased_origin"] is True

    reply = propose(client, replay, option, outside_basis=False)
    assert reply.status_code == 200, reply.text
    accepted = respond(client, replay, reply.json()["revision"])
    assert accepted.status_code == 200, accepted.text
    assignments = rows(db, CapacityAssignment, demand)
    assert len(assignments) == 3
    assert {
        row.employee_id: (row.start_date, row.due_date) for row in assignments
    } == expected
    assert all(row.allocation_percent == 50 for row in assignments)


@pytest.mark.parametrize("replay", ["unavailability-01"], indirect=True)
def test_optimized_option_rejects_work_after_commitment(
    client: TestClient, db: Session, replay: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    url, _, auth, demand = replay
    late = next(
        o for o in create_case(client, replay)["options"] if o["kind"] == "optimized"
    )
    (leave,) = rows(db, CapacityUnavailability, demand)
    restored_leave = CapacityUnavailability.model_validate(
        leave.model_dump(exclude={"id"})
    )
    db.delete(leave)
    db.commit()
    direct = next(
        o for o in create_case(client, replay)["options"] if o["kind"] == "direct"
    )
    reply = propose(client, replay, direct, outside_basis=False)
    assert reply.status_code == 200, reply.text
    accepted = respond(client, replay, reply.json()["revision"])
    assert accepted.status_code == 200, accepted.text
    db.add(restored_leave)
    db.commit()
    db.refresh(demand)
    assert demand.committed_end_date == demand.window_end

    created = create_case(client, replay)
    assert created["data"]["demand"]["change"] is True
    assert all(o["kind"] != "optimized" for o in created["options"])

    def late_solution(_solver: CpSatSolver, roles: Sequence[Role]) -> Solution:
        return Solution({roles[0].key: late["params"]["assignees"]}, ())

    monkeypatch.setattr(CpSatSolver, "solve", late_solution)
    rechecked = create_case(client, replay)
    assert all(o["kind"] != "optimized" for o in rechecked["options"])

    db.delete(restored_leave)
    db.commit()
    available = create_case(client, replay)
    (late_row,) = late["params"]["assignees"]
    manual = client.post(
        f"{url}/planning/cases/{available['id']}/options",
        headers=auth["b"],
        json={
            "assignees": [{**late_row, "start_date": demand.window_start.isoformat()}]
        },
    )
    assert manual.status_code == 200, manual.text
    change = submit_change(client, replay, manual.json(), outside_basis=False)
    assert change.status_code == 409, change.text
    assert "推迟完成日期请走延期审批" in change.text


@pytest.mark.parametrize("failure", ["timeout", "error", "invalid"])
@pytest.mark.parametrize("replay", ["unavailability-01"], indirect=True)
def test_optimized_option_failure_keeps_rule_options(
    client: TestClient,
    replay: tuple,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure: str,
) -> None:
    baseline = create_case(client, replay)
    assert any(o["kind"] == "optimized" for o in baseline["options"])
    time_limits: list[float] = []

    def solve(
        self: cp_model.CpSolver, _model: cp_model.CpModel
    ) -> cp_model.CpSolverStatus:
        time_limits.append(self.parameters.max_time_in_seconds)
        if failure == "error":
            raise ValueError("private solver input")
        return cp_model.UNKNOWN

    def invalid(_solver: CpSatSolver, roles: Sequence[Role]) -> Solution:
        role = roles[0]
        candidate = next(p for p in role.snapshot["candidates"] if p["in_basis"])
        # The qualified candidate is unavailable during the original first week.
        window = role.snapshot["demand"]
        return Solution(
            {
                role.key: [
                    {
                        "employee_id": candidate["employee_id"],
                        "allocation_percent": window["allocation_percent"],
                        "start_date": window["window_start"],
                        "end_date": window["window_end"],
                    }
                ]
            },
            (),
        )

    if failure == "invalid":
        monkeypatch.setattr(CpSatSolver, "solve", invalid)
    else:
        monkeypatch.setattr(cp_model.CpSolver, "solve", solve)

    result = create_case(client, replay)
    if failure != "invalid":
        (limit,) = time_limits
        assert 0 < limit <= SOLVE_TIMEOUT_SECONDS
    assert [o["kind"] for o in result["options"]] == [
        o["kind"] for o in baseline["options"] if o["kind"] != "optimized"
    ]
    assert "private solver input" not in str(result)
    assert "private solver input" not in caplog.text
