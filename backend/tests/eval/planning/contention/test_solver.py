import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import yaml
from fastapi.testclient import TestClient
from ortools.sat.python import cp_model
from sqlmodel import Session, select

from app.domains.org.models import Organization
from app.domains.planning import cpsat
from app.domains.planning.cpsat import (
    SOLVE_TIMEOUT_SECONDS,
    CpSatSolver,
    SolverIncomplete,
)
from app.domains.planning.solver import Role, RuleSolver, Solution, full_plan
from app.models import User
from scripts.eval_solver import metrics
from scripts.export_planning_case import anonymize, export_saved
from tests.eval.planning.contention.cases import HERE, load_cases
from tests.eval.planning.test_planning_eval import HERE as PLANNING
from tests.eval.planning.test_planning_eval import _cleanup, _setup

EVAL_CASES = load_cases()
REPLAYS = ("multi-capability-01", "unavailability-01")


def test_case_coverage() -> None:
    cases = yaml.safe_load((HERE / "synthetic.yaml").read_text())
    assert len({case["id"] for case in cases}) >= 12
    assert sum(key.startswith("replay-") for key in EVAL_CASES) >= 2
    for case in cases:
        assert case["constraint"]
        assert 8 <= case["people"] <= 30
        assert 2 <= len(case["projects"]) <= 4
        assert all(3 <= len(project) <= 6 for project in case["projects"])
        assert case["newcomers"] and case["unavailable"]
        assert any(row[-1] for project in case["projects"] for row in project)


@pytest.mark.parametrize("key", list(EVAL_CASES))
def test_contention(key: str) -> None:
    roles = EVAL_CASES[key]
    rule = RuleSolver().solve(roles)
    cp_sat = CpSatSolver().solve(roles)
    assert rule == RuleSolver().solve(roles)
    assert cp_sat == CpSatSolver().solve(roles)
    rule_stats, cp_stats = metrics(roles, rule), metrics(roles, cp_sat)
    assert rule_stats["violations"] == cp_stats["violations"] == 0
    assert len(cp_sat.unfilled) + cp_stats["filled"] == len(roles)
    assert (cp_stats["filled"], -cp_stats["delay"]) >= (
        rule_stats["filled"],
        -rule_stats["delay"],
    )
    if key.startswith("trap-"):
        assert (cp_stats["filled"], -cp_stats["delay"]) > (
            rule_stats["filled"],
            -rule_stats["delay"] + 4,
        )


def test_audit_detects_double_booking() -> None:
    early, _, _, later, *_ = EVAL_CASES["trap-primary"]
    rule = RuleSolver()
    independently_filled = rule.solve([early]).filled | rule.solve([later]).filled
    assert (
        metrics([early, later], Solution(independently_filled, ()))["violations"] == 2
    )


def test_model_deadline_stops_on_first_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    role = EVAL_CASES["trap-primary"][0]
    # Expire on the first candidate check, after creating only the offset variable.
    readings = iter((0.0, 0.0, 0.0, 0.0, float(SOLVE_TIMEOUT_SECONDS)))
    monkeypatch.setattr(cpsat, "perf_counter", lambda: next(readings))
    created: list[str] = []
    new_bool_var = cp_model.CpModel.new_bool_var

    def record(model: cp_model.CpModel, name: str) -> cp_model.IntVar:
        created.append(name)
        return new_bool_var(model, name)

    def unexpected_solve(*_args: object) -> None:
        pytest.fail("Expired model reached the solver")

    monkeypatch.setattr(cp_model.CpModel, "new_bool_var", record)
    monkeypatch.setattr(cp_model.CpSolver, "solve", unexpected_solve)
    with pytest.raises(SolverIncomplete, match="time budget exhausted"):
        CpSatSolver().solve([role])
    assert created == [f"{role.key}:0"]


def test_replayed_commitment_blocks_late_optimization() -> None:
    (initial,) = EVAL_CASES["replay-unavailability-01"]
    rows = CpSatSolver().solve([initial]).filled[initial.key]
    assert rows == [
        {
            "employee_id": "001",
            "allocation_percent": 50,
            "start_date": "2026-11-23",
            "end_date": "2026-12-06",
        }
    ]
    demand = initial.snapshot["demand"]
    committed = replace(
        initial,
        snapshot={
            **initial.snapshot,
            "demand": {
                **demand,
                "change": True,
                "committed_start_date": demand["window_start"],
                "committed_end_date": demand["window_end"],
            },
        },
    )
    assert not full_plan(committed.snapshot, rows)
    successor = replace(initial, key="2", predecessors=(committed.key,))
    for solver in (RuleSolver(), CpSatSolver()):
        assert solver.solve([committed, successor]) == Solution(
            {}, (committed.key, successor.key)
        )


def test_extended_commitment_leaves_capacity_for_overlapping_role() -> None:
    original, _, _, overlap, *_ = EVAL_CASES["trap-primary"]
    demand = original.snapshot["demand"]
    change = Role(
        original.key,
        {
            **original.snapshot,
            # Capacity is known only up to the original end, so a later window
            # matching the extended commitment is unavailable.
            "candidates": [
                {**person, "weeks": person["weeks"][:2]}
                for person in original.snapshot["candidates"]
            ],
            "demand": {
                **demand,
                "change": True,
                "committed_start_date": demand["window_start"],
                "committed_end_date": "2026-12-06",
            },
        },
    )
    roles = [change, overlap]
    for solver in (RuleSolver(), CpSatSolver()):
        assert not full_plan(
            change.snapshot, solver.solve([original]).filled[original.key]
        )
        result = solver.solve(roles)
        expected = solver.solve([overlap])
        assert list(expected.filled) == [overlap.key]
        assert result == Solution(expected.filled, (change.key,))
        stats = metrics(roles, result)
        assert stats == metrics([overlap], expected)
        assert stats["violations"] == 0


@pytest.mark.parametrize("name", REPLAYS)
def test_export_saved_replay(
    name: str,
    db: Session,
    client: TestClient,
    tmp_path: Path,
) -> None:
    raw = yaml.safe_load((PLANNING / "cases" / f"{name}.yaml").read_text())
    users = set(db.exec(select(User.id)).all())
    org = Organization(slug=f"solver-eval-{uuid4().hex}", name="Solver evaluation")
    db.add(org)
    db.flush()
    org_id = org.id
    try:
        _, employees, auth, demand, _ = _setup(db, raw, org)
        response = client.post(
            f"/api/v1/orgs/{org_id}/planning/demands/{demand.id}/cases",
            headers=auth["b_manager"],
        )
        assert response.status_code == 200, response.text
        result = response.json()
        data = result["data"]
        target = tmp_path / f"replay-{name}.json"
        export_saved(db, org_id, UUID(result["snapshot_id"]), target)
        exported = target.read_text()
        for person in employees.values():
            assert person.display_name not in exported
            assert person.work_email not in exported
            assert str(person.id) not in exported
        original = [Role("1", data)]
        replay = [Role("1", json.loads(exported))]
        rule = RuleSolver()
        assert metrics(original, rule.solve(original)) == metrics(
            replay, rule.solve(replay)
        )
        if os.environ.get("SOLVER_EXPORT_REPLAYS") == "1":
            (HERE / target.name).write_text(exported)
    finally:
        _cleanup(db, org_id, users)


def test_export_omits_untrusted_text() -> None:
    data: dict[str, Any] = EVAL_CASES["trap-extra"][-3].snapshot
    secret = "private@example.com full name free text"
    demand = {**data["demand"], "description": secret}
    first, *others = data["candidates"]
    result = anonymize(
        {
            **data,
            "demand": demand,
            "candidates": [
                {
                    **first,
                    "employee_id": secret,
                    "department_id": secret,
                    "name": secret,
                    "work_email": secret,
                },
                *others,
            ],
            "comment": secret,
        }
    )
    exported = json.dumps(result)
    assert secret not in exported
    assert demand["capability_key"] not in exported
    assert all(
        person["employee_id"].isdigit() and person["department_id"].isdigit()
        for person in result["candidates"]
    )
