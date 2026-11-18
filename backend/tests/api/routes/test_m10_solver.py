from collections.abc import Generator, Sequence
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
)
from app.domains.org.models import Organization
from app.domains.planning.cpsat import SOLVE_TIMEOUT_SECONDS, CpSatSolver
from app.domains.planning.solver import Role, Solution
from app.models import User
from scripts.eval_solver import metrics
from tests.api.routes.test_m9_proposal import propose, respond, rows
from tests.eval.planning.test_planning_eval import HERE, _cleanup, _setup


@pytest.fixture
def replay(db: Session, request: pytest.FixtureRequest) -> Generator[tuple]:
    name = getattr(request, "param", "multi-capability-01")
    raw = yaml.safe_load((HERE / "cases" / f"{name}.yaml").read_text())
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
    assert option["verdict"] == "conditional"
    assert "需求方同意分阶段交付" in option["summary"]["approvers"]
    assert ("需求方接受新日期" in option["summary"]["approvers"]) == bool(delay_days)
    assert metrics(
        [Role("1", created["data"])],
        Solution({"1": option["params"]["assignees"]}, ()),
    ) == {"filled": 1, "delay": delay_days, "newcomer": 0, "violations": 0}


def test_optimized_option_uses_m9_reply_and_accept(
    client: TestClient, db: Session, replay: tuple
) -> None:
    _, _, _, demand = replay
    created = create_case(client, replay)
    (option,) = [o for o in created["options"] if o["kind"] == "optimized"]
    assert not rows(db, CapacityProposal, demand)
    assert not rows(db, CapacityAssignment, demand)

    reply = propose(client, replay, option, outside_basis=False)
    assert reply.status_code == 200, reply.text
    assert reply.json()["proposal"]["status"] == "pending"
    (proposal,) = rows(db, CapacityProposal, demand)
    assert str(proposal.source_option_id) == option["id"]
    assert not rows(db, CapacityAssignment, demand)

    accepted = respond(client, replay, reply.json()["revision"])
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["proposal"]["status"] == "committed"
    assert len(rows(db, CapacityAssignment, demand)) == len(
        option["params"]["assignees"]
    )


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
