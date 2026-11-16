"""M8 coordination comparison on the frozen M7 cases."""

import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from time import monotonic
from typing import Any, cast
from unittest.mock import patch
from uuid import UUID

import pytest
import yaml
from pydantic import ValidationError
from sqlmodel import Session, col, select

from app.authz.deps import get_org_principal
from app.domains.bot import core, store, tools
from app.domains.bot.prompts import system_prompt
from app.domains.im.models import ImNotification
from app.domains.model import pi_client
from app.domains.model.models import ModelSetting
from app.domains.org.models import Organization, OrgMembership
from app.domains.planning import (
    service,
    simulator,
    validator,
)
from app.domains.planning import (
    snapshot as planning_snapshot,
)
from app.domains.planning.models import PlanningCase, PlanningOption, PlanningSnapshot
from app.domains.planning.schemas import OptionInput, OptionPublic, ReviseInput
from app.models import User
from tests.api.routes.test_m7_planning_readonly import business_rows
from tests.eval.planning.test_planning_eval import _cleanup, _setup

ALL_CASES = sorted((Path(__file__).parents[1] / "planning/cases").glob("*.yaml"))
if selected_case := os.getenv("COORDINATION_EVAL_CASE"):
    CASES = [path for path in ALL_CASES if path.stem == selected_case]
elif os.getenv("COORDINATION_EVAL_ALL"):
    CASES = ALL_CASES
else:
    categories: set[str] = set()
    CASES = []
    for path in ALL_CASES:
        category = yaml.safe_load(path.read_text())["category"]
        if category not in categories:
            CASES.append(path)
            categories.add(category)
ORDER = {"feasible": 0, "conditional": 1, "insufficient_data": 2, "infeasible": 3}
READ_ONLY = {
    "read_coordination_context",
    "inspect_capacity",
    "search_qualified_candidates",
}


def parse_option(text: str) -> OptionInput | None:
    for block in re.findall(r"```(?:json)?\s*(.*?)\s*```", text, re.S | re.I):
        try:
            return OptionInput.model_validate(json.loads(block))
        except ValueError, ValidationError:
            continue
    return None


def parse_uuid(value: Any) -> UUID | None:
    try:
        return UUID(str(value))
    except ValueError:
        return None


def best_verdict(options: list[dict[str, Any]]) -> str:
    return min((str(option["verdict"]) for option in options), key=ORDER.__getitem__)


def observed_rows(db: Session, org_id: UUID) -> dict[str, str]:
    rows = business_rows(db, org_id)
    notifications = db.exec(
        select(ImNotification).where(ImNotification.org_id == org_id)
    ).all()
    rows["im_notification"] = json.dumps(
        [n.model_dump(mode="json") for n in notifications], sort_keys=True
    )
    return rows


def plan_ids(db: Session, org_id: UUID) -> tuple[set[UUID], set[UUID], set[UUID]]:
    return (
        set(
            db.exec(select(PlanningCase.id).where(PlanningCase.org_id == org_id)).all()
        ),
        set(
            db.exec(
                select(PlanningSnapshot.id).where(PlanningSnapshot.org_id == org_id)
            ).all()
        ),
        set(
            db.exec(
                select(PlanningOption.id).where(PlanningOption.org_id == org_id)
            ).all()
        ),
    )


def free_capacity(person: dict[str, Any]) -> int:
    return sum(
        max(
            0,
            week["capacity_percent"] - week["other_percent"] - week["platform_percent"],
        )
        for week in person["weeks"]
        if week["status"] == "confirmed" and week["other_percent"] is not None
    )


def scripted_option(data: dict[str, Any]) -> dict[str, Any] | None:
    candidates = data["candidates"]
    if not candidates:
        return None
    demand = data["demand"]
    return {
        "assignees": [
            {
                "employee_id": person["employee_id"],
                "allocation_percent": demand["allocation_percent"],
                "start_date": demand["window_start"],
                "end_date": demand["window_end"],
            }
            for person in candidates[: demand["headcount"]]
        ]
    }


def manager_revisions(
    db: Session,
    principal: Any,
    case_id: UUID | None,
    demand_id: UUID,
    body: OptionInput,
    target: str,
) -> str:
    """Apply the fixed manager policy through S3, rolling back every evaluation edit."""
    with db.begin_nested() as savepoint:
        if case_id is None:
            case_id = service.create_case(principal, db, demand_id).id
        access = service.authorized_case(principal, db, case_id)
        option = service.create_option(principal, db, access, body)
        count = 0
        while ORDER[str(option.verdict)] > ORDER[target] and count < 5:
            snapshot = cast("dict[str, Any]", access[1].data)
            assignees = cast("list[dict[str, Any]]", option.params["assignees"])
            findings = option.findings
            bad = next(
                (
                    f
                    for f in findings
                    if f["code"]
                    in {"level_too_low", "inactive", "over_capacity", "overallocated"}
                    and f.get("employee_id")
                ),
                None,
            )
            change: ReviseInput | None = None
            if bad:
                current = {a["employee_id"] for a in assignees}
                qualified = [
                    p
                    for p in snapshot["candidates"]
                    if p["employee_id"] not in current
                    and p["employment_status"] == "active"
                    and p["level"] in snapshot["rules"]["levels"]
                    and snapshot["rules"]["levels"].index(p["level"])
                    >= snapshot["rules"]["levels"].index(
                        snapshot["demand"]["minimum_level"]
                    )
                ]
                if qualified:
                    # Weekly free capacity is the tie breaker; no employee ranking leaves this evaluator.
                    candidate = max(
                        qualified,
                        key=lambda p: (free_capacity(p), p["employee_id"]),
                    )
                    change = ReviseInput(
                        change="replace",
                        from_employee_id=UUID(str(bad["employee_id"])),
                        to_employee_id=UUID(candidate["employee_id"]),
                    )
            if change is None and any(f["code"] == "outside_window" for f in findings):
                demand = snapshot["demand"]
                change = ReviseInput(
                    change="dates",
                    start_date=demand["window_start"],
                    end_date=demand["window_end"],
                )
            if change is None:
                percent = min(a["allocation_percent"] for a in assignees)
                if percent > 10:
                    change = ReviseInput(
                        change="allocation", allocation_percent=max(10, percent - 10)
                    )
            if change is None:
                break
            try:
                option = service.revise_option(principal, db, access, option.id, change)
            except service.PlanningInvalidRevision, service.PlanningLocked:
                break
            count += 1
        result = str(count) if ORDER[str(option.verdict)] <= ORDER[target] else "5+"
        savepoint.rollback()
    db.expire_all()
    return result


def evaluate(
    db: Session, raw: dict[str, Any], mode: str, fake: bool, model: str, thinking: str
) -> dict[str, Any]:
    existing_users = set(db.exec(select(User.id)).all())
    org = Organization(slug=f"coord-eval-{raw['id']}", name="Coordination eval")
    db.add(org)
    db.flush()
    org_id = org.id
    row: dict[str, Any] = {
        "id": raw["id"],
        "category": raw["category"],
        "mode": mode,
        "status": "passed",
    }
    try:
        _, employees, _, demand, _ = _setup(db, raw, org)
        b_code = raw["roles"][1]
        manager = db.exec(
            select(User)
            .join(OrgMembership, col(OrgMembership.user_id) == col(User.id))
            .where(
                OrgMembership.org_id == org_id,
                OrgMembership.employee_id == employees[b_code].id,
            )
        ).one()
        principal = get_org_principal(org_id, manager, db)
        db.add(
            ModelSetting(
                org_id=org_id,
                updated_by_user_id=manager.id,
                enabled=True,
                kind="local" if fake else "chatgpt_subscription",
                provider="ollama" if fake else None,
                model="synthetic" if fake else model,
                base_url="http://localhost:11434" if fake else None,
                reasoning_effort=thinking,
            )
        )
        db.commit()
        baseline_data, _, departments = planning_snapshot.build(db, principal, demand)
        baseline_options, _ = simulator.simulate(baseline_data)
        if departments:
            baseline_options.append(simulator.borrow(baseline_data, departments))
        before = observed_rows(db, org_id)
        before_plans = plan_ids(db, org_id)
        target = best_verdict(baseline_options)
        row.update(
            baseline=target,
            newcomer_baseline=any(o["kind"] == "newcomer" for o in baseline_options),
        )
        session = store.create_session(db, principal)
        original_definitions, original_call, original_turn = (
            tools.definitions,
            tools.call,
            pi_client.run_turn,
        )
        original_prompt = system_prompt
        seen: list[tuple[str, dict[str, Any], Any]] = []
        stats = {"model_calls": 0, "tool_calls": 0}
        rejected: list[str] = []

        def definitions(*args: Any, **kwargs: Any) -> list[pi_client.ToolDefinition]:
            available = original_definitions(*args, **kwargs)
            selected = [
                t
                for t in available
                if t.name == "coordination"
                or (mode == "agent_validator" and t.name == "prepare_plan_proposal")
            ]
            if mode == "agent":
                schema = cast("dict[str, Any]", selected[0].parameters)
                schema["properties"]["function"]["enum"] = sorted(READ_ONLY)
            return selected

        def guarded_call(*args: Any, **kwargs: Any) -> Any:
            name, parameters = args[4], args[5]
            if name not in {"coordination", "prepare_plan_proposal"} or (
                mode == "agent"
                and (
                    name != "coordination"
                    or parameters.get("function") not in READ_ONLY
                )
            ):
                rejected.append(name)
                return {"error": "评测模式禁止该工具"}
            value = original_call(*args, **kwargs)
            seen.append((name, dict(parameters), value))
            return value

        def turn(config: Any, **kwargs: Any) -> pi_client.TurnResult:
            offered = {t.name for t in kwargs["tools"]}
            assert offered <= {"coordination", "prepare_plan_proposal"}
            if mode == "agent":
                assert offered == {"coordination"}
            callback: Callable[..., Any] = kwargs["on_tool_call"]

            def count_call(name: str, arguments: dict[str, Any]) -> Any:
                stats["tool_calls"] += 1
                return callback(name, arguments)

            kwargs["on_tool_call"] = count_call
            if fake:
                request = {
                    "function": "read_coordination_context",
                    "arguments": {"demand_id": str(demand.id)},
                }
                count_call("coordination", request)
                request["function"] = "inspect_capacity"
                count_call("coordination", request)
                request["function"] = "search_qualified_candidates"
                count_call("coordination", request)
                if mode == "agent":
                    body = scripted_option(baseline_data)
                    text = (
                        "```json\n"
                        + json.dumps(body or {}, ensure_ascii=False)
                        + "\n```"
                    )
                else:
                    value = count_call(
                        "coordination",
                        {
                            "function": "simulate_plan",
                            "arguments": {"demand_id": str(demand.id)},
                        },
                    )
                    options = (
                        value.get("root", value).get("options", [])
                        if isinstance(value, dict)
                        else []
                    )
                    labels = {
                        "可行": "feasible",
                        "有条件可行": "conditional",
                        "数据不足": "insufficient_data",
                        "不可行": "infeasible",
                    }
                    best = min(
                        options, key=lambda o: ORDER[labels[o["verdict"]]], default=None
                    )
                    if raw["category"] == "借调":
                        best = next(
                            (o for o in options if o["kind"] == "向其他部门借调"),
                            best,
                        )
                    text = f"推荐方案 {best['id']}" if best else "没有可推荐方案"
                result = pi_client.TurnResult(
                    text=text,
                    stop_reason="stop",
                    model_calls=1,
                    tool_calls=stats["tool_calls"],
                )
            else:
                result = original_turn(config, **kwargs)
            stats["model_calls"] += result.model_calls
            return result

        started = monotonic()
        protocol = (
            "评测协议：只能使用协调读取工具调查。最后一条回复必须包含一个 JSON 代码块，"
            '内容是 OptionInput 格式的 {"assignees": [...]}，人员 ID 必须来自工具结果。'
            if mode == "agent"
            else "评测协议：先调用 coordination.simulate_plan，再根据 findings 修改或选择方案。"
            "最后一条回复须明确点名所推荐方案的 UUID，必须是最后一次模拟返回的方案。"
        )
        with (
            patch.object(tools, "definitions", definitions),
            patch.object(tools, "call", guarded_call),
            patch.object(pi_client, "run_turn", turn),
            patch.object(
                core,
                "system_prompt",
                lambda *a, **kw: original_prompt(*a, **kw) + "\n" + protocol,
            ),
        ):
            response = core.turn(
                db,
                principal,
                session.id,
                f"请为这个需求协调人手，需求编号 {demand.id}。需要{demand.headcount}人，每人投入{demand.allocation_percent}%，期限到{demand.window_end}。",
            )
        row.update(
            stats,
            seconds=round(monotonic() - started, 3),
            rejected_tools=len(rejected),
            tool_functions=[args.get("function", name) for name, args, _ in seen],
        )
        assert observed_rows(db, org_id) == before, (
            "Capacity, audit or notification changed"
        )
        if mode == "agent":
            assert plan_ids(db, org_id) == before_plans, "Pure Agent wrote a plan row"
            contexts = [
                value.get("root", value)
                for name, args, value in seen
                if name == "coordination"
                and args.get("function") == "read_coordination_context"
                and isinstance(value, dict)
            ]
            assert all("latest_case" not in context for context in contexts), (
                "Pure Agent saw a rule-engine proposal"
            )
        current_case_id = db.exec(
            select(PlanningCase.id).where(
                PlanningCase.org_id == org_id, PlanningCase.demand_id == demand.id
            )
        ).one_or_none()
        output = "\n".join(str(m.text) for m in response.messages if hasattr(m, "text"))
        if "正在起草画像" in output:
            row["status"] = "deferred:drafting_paused"
        elif "模型额度暂时用完" in output:
            row["status"] = "deferred:subscription_rate_limit"
        elif "模型服务暂时不可用" in output:
            row["status"] = "failed:model_unavailable"
        selected: OptionInput | None = None
        recommended: OptionPublic | None = None
        if row["status"] == "passed":
            if mode == "agent":
                selected = parse_option(output)
            else:
                prepared = [
                    option_id
                    for name, args, value in seen
                    if name == "prepare_plan_proposal"
                    and isinstance(value, dict)
                    and "error" not in value
                    if (option_id := parse_uuid(args.get("option_id"))) is not None
                ]
                simulated = [
                    (name, value)
                    for name, args, value in seen
                    if name == "coordination"
                    and args.get("function") == "simulate_plan"
                ]
                chosen = prepared[-1] if prepared else None
                if chosen is None and simulated:
                    value = simulated[-1][1]
                    data = value.get("root", value) if isinstance(value, dict) else {}
                    options = data.get("options", [data.get("option", {})])
                    chosen = next(
                        (
                            UUID(o["id"])
                            for o in options
                            if o.get("id") and o["id"] in output
                        ),
                        None,
                    )
                recommended = next(
                    (
                        o
                        for o in (
                            service.get_case(principal, db, current_case_id).options
                            if current_case_id is not None
                            else []
                        )
                        if o.id == chosen
                    ),
                    None,
                )
                if recommended and recommended.params.get("assignees"):
                    selected = OptionInput.model_validate(recommended.params)
            row["parse_failure"] = selected is None and recommended is None
            if selected:
                params = selected.model_dump(mode="json")
                candidate_ids = {p["employee_id"] for p in baseline_data["candidates"]}
                row["outside_candidates"] = any(
                    a["employee_id"] not in candidate_ids for a in params["assignees"]
                )
                result = validator.validate(baseline_data, params)
                row["verdict"] = result.verdict
                row["violation"] = (
                    result.verdict == "infeasible"
                    or ORDER[result.verdict] > ORDER[target]
                )
                row["newcomer"] = any(
                    p["newcomer"]
                    and p["employee_id"]
                    in {a["employee_id"] for a in params["assignees"]}
                    for p in baseline_data["candidates"]
                )
                if not row["outside_candidates"]:
                    row["manager_changes"] = manager_revisions(
                        db, principal, current_case_id, demand.id, selected, target
                    )
                else:
                    row["manager_changes"] = "5+"
            elif recommended is not None:
                verdict = str(recommended.verdict)
                row.update(
                    option_kind=str(recommended.kind),
                    verdict=verdict,
                    violation=verdict == "infeasible" or ORDER[verdict] > ORDER[target],
                    outside_candidates=False,
                    newcomer=False,
                    manager_changes="0" if ORDER[verdict] <= ORDER[target] else "5+",
                )
            else:
                row.update(
                    outside_candidates=False,
                    violation=True,
                    newcomer=False,
                    manager_changes="—",
                )
        assert observed_rows(db, org_id) == before, (
            "Manager evaluation changed business rows"
        )
        if mode == "agent":
            assert plan_ids(db, org_id) == before_plans, "Pure Agent left a plan row"
        # The B manager may see their own team only; cross-department people never enter tool output.
        foreign = [
            e
            for code, e in employees.items()
            if e.department_id != employees[b_code].department_id
        ]
        foreign_user_ids = db.exec(
            select(OrgMembership.user_id).where(
                OrgMembership.org_id == org_id,
                col(OrgMembership.employee_id).in_([e.id for e in foreign]),
            )
        ).all()
        projected = json.dumps([value for _, _, value in seen], ensure_ascii=False)
        assert not any(
            e.display_name in projected
            or e.employee_code in projected
            or str(e.id) in projected
            for e in foreign
        ) and not any(str(user_id) in projected for user_id in foreign_user_ids), (
            "Cross-department personal field leaked"
        )
        return row
    finally:
        _cleanup(db, org_id, existing_users)


@pytest.mark.parametrize("path", CASES, ids=[p.stem for p in CASES])
def test_coordination_eval(path: Path, db: Session) -> None:
    raw = yaml.safe_load(path.read_text())
    for mode in (
        os.getenv("COORDINATION_EVAL_MODE", "both")
        .replace("both", "agent,agent_validator")
        .split(",")
    ):
        result = evaluate(
            db,
            raw,
            mode,
            os.getenv("COORDINATION_EVAL_FAKE", "1") == "1",
            os.getenv("COORDINATION_EVAL_MODEL", "chatgpt/gpt-6.1-sol"),
            os.getenv("COORDINATION_EVAL_THINKING", "high"),
        )
        if output := os.getenv("COORDINATION_EVAL_RESULT"):
            with Path(output).open("a") as stream:
                stream.write(json.dumps(result, ensure_ascii=False) + "\n")
        if (
            raw["category"] == "借调"
            and mode == "agent_validator"
            and os.getenv("COORDINATION_EVAL_FAKE", "1") == "1"
        ):
            assert result["parse_failure"] is False, result
            assert result["option_kind"] == "borrow", result
        assert result["status"] in {
            "passed",
            "deferred:drafting_paused",
            "deferred:subscription_rate_limit",
        }, result


def test_parse_option_rejects_invalid_json() -> None:
    assert parse_option("```json\n{}\n```") is None
    assert parse_option("没有方案") is None
    assert (
        parse_option(
            '```json\n{"assignees":[{"employee_id":"00000000-0000-0000-0000-000000000001",'
            '"allocation_percent":50,"start_date":"2026-11-16","end_date":"2026-11-30"}]}\n```'
        )
        is not None
    )


def test_invalid_proposal_id_is_not_selected() -> None:
    assert parse_uuid("invalid") is None


def test_free_capacity_uses_confirmed_week_load() -> None:
    assert (
        free_capacity(
            {
                "weeks": [
                    {
                        "status": "confirmed",
                        "capacity_percent": 100,
                        "other_percent": 20,
                        "platform_percent": 30,
                    },
                    {
                        "status": "unconfirmed",
                        "capacity_percent": 100,
                        "other_percent": 0,
                        "platform_percent": 0,
                    },
                ]
            }
        )
        == 50
    )
