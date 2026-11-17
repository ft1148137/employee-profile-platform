"""Caller-scoped coordination views over Capacity and Planning services."""

from collections.abc import Callable
from typing import Any, Self, cast
from uuid import UUID

from pydantic import BaseModel, Field, JsonValue, PrivateAttr, RootModel
from sqlmodel import Session, col, select

from app.authz.principal import Principal
from app.domains.capability.schemas import DictionaryRubric
from app.domains.capability.service import get_schema, list_capabilities
from app.domains.capacity.demand import (
    demand_row,
    department_reference,
    get_demand,
    get_demand_basis,
    reference_for_viewer,
    represents_department,
)
from app.domains.capacity.models import (
    CapacityDemand,
    CapacityDemandEvent,
    DemandEventKind,
    DemandOutcome,
    DemandStatus,
)
from app.domains.employee_tools import EmployeeTool
from app.domains.org.models import OrgMembership
from app.domains.planning import service, snapshot
from app.domains.planning.models import PlanningCase
from app.domains.planning.schemas import (
    AssigneeInput,
    CasePublic,
    OptionInput,
    OptionPublic,
)


class Result(RootModel[dict[str, Any]]):
    _read_employee_ids: set[str] | None = PrivateAttr(default=None)

    def with_reads(self, ids: set[str]) -> Self:
        self._read_employee_ids = ids
        return self


class DemandIdInput(BaseModel):
    demand_id: UUID


class SimulateInput(DemandIdInput):
    assignees: list[AssigneeInput] | None = Field(default=None, min_length=1)


class CompareInput(BaseModel):
    case_id: UUID
    option_ids: list[UUID] = Field(min_length=1, max_length=10)


STATUS_LABELS = {
    DemandStatus.SUBMITTED: "待答复",
    DemandStatus.REPLIED: "已答复",
    DemandStatus.ACCEPTED: "已接受",
    DemandStatus.WITHDRAWN: "已撤回",
}
EVENT_LABELS = {
    DemandEventKind.SUBMITTED: "已提交",
    DemandEventKind.REPLIED: "已答复",
    DemandEventKind.ACCEPTED: "已接受",
    DemandEventKind.FOLLOW_UP: "已追问",
    DemandEventKind.WITHDRAWN: "已撤回",
    DemandEventKind.COMMITMENT_CHANGED: "承诺日期已调整",
}
OUTCOME_LABELS = {
    DemandOutcome.CAN_DO: "能做",
    DemandOutcome.PARTIAL: "能做一部分",
    DemandOutcome.NOT_NOW: "暂时不能",
}
WEEK_LABELS = {"confirmed": "已确认", "stale": "需重新确认", "unconfirmed": "未确认"}


def _case_id(p: Principal, db: Session, demand_id: UUID) -> UUID | None:
    return db.exec(
        select(PlanningCase.id).where(
            PlanningCase.org_id == p.org_id, PlanningCase.demand_id == demand_id
        )
    ).one_or_none()


def _department_view(p: Principal, db: Session, row: CapacityDemand) -> dict[str, Any]:
    reference = reference_for_viewer(
        p,
        row.target_department_id,
        department_reference(
            db,
            p.org_id,
            row.target_department_id,
            row.capability_key,
            row.minimum_level,
        ),
    )
    return reference.model_dump(mode="json")


def _demand_view(
    p: Principal,
    db: Session,
    demand_id: UUID,
    *,
    on_read: Callable[[set[str]], None] | None = None,
) -> dict[str, Any]:
    row = demand_row(p, db, demand_id)
    detail = get_demand(p, db, row.id).model_dump(mode="json")
    if on_read:
        on_read(
            {
                str(id)
                for id in db.exec(
                    select(OrgMembership.employee_id)
                    .join(
                        CapacityDemandEvent,
                        (col(CapacityDemandEvent.org_id) == OrgMembership.org_id)
                        & (
                            col(CapacityDemandEvent.actor_user_id)
                            == OrgMembership.user_id
                        ),
                    )
                    .where(
                        OrgMembership.org_id == p.org_id,
                        CapacityDemandEvent.demand_id == row.id,
                        CapacityDemandEvent.kind == DemandEventKind.REPLIED,
                    )
                ).all()
                if id is not None
            }
            if represents_department(p, row.target_department_id)
            else set()
        )
    detail.pop("capability_key", None)
    detail["status"] = STATUS_LABELS[row.status]
    levels = next(
        (
            capability.level_rubric
            for capability in list_capabilities(
                db, p.org_id, get_schema(db, p.org_id).id
            )
            if capability.key == row.capability_key
        ),
        None,
    )
    if levels and row.minimum_level:
        rubric = DictionaryRubric.model_validate(levels)
        detail["minimum_level"] = next(
            (level.label for level in rubric.levels if level.key == row.minimum_level),
            "未定级",
        )
    elif row.minimum_level:
        detail["minimum_level"] = "未定级"
    if not represents_department(p, row.target_department_id):
        detail.pop("basis_url", None)
        for event in detail["events"]:
            event.pop("responder_name", None)
            if event["kind"] in {
                DemandEventKind.REPLIED,
                DemandEventKind.COMMITMENT_CHANGED,
            }:
                if event.get("text"):
                    detail["reply_notice"] = (
                        "没有读取对方答复正文里的条件，请到需求详情查看"
                    )
                    detail["demand_url"] = f"/capacity?tab=outgoing&demand={row.id}"
                event.pop("text", None)
    for event in detail["events"]:
        event["kind"] = EVENT_LABELS[event["kind"]]
        if event.get("outcome"):
            event["outcome"] = OUTCOME_LABELS[event["outcome"]]
        event.pop("via", None)
    return detail


def _weeks(person: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {**week, "status": WEEK_LABELS.get(week["status"], "未确认")}
        for week in person["weeks"]
    ]


def _option(option: OptionPublic, case: CasePublic) -> dict[str, Any]:
    assignees = cast("list[dict[str, JsonValue]]", option.params.get("assignees", []))
    names = {str(person.employee_id): person.display_name for person in case.people}
    return {
        "id": str(option.id),
        "kind": option.kind_label,
        "verdict": option.verdict_label,
        "dates": [
            {"start_date": a["start_date"], "end_date": a["end_date"]}
            for a in assignees
        ],
        "assignees": [
            {**assignee, "name": names.get(str(assignee["employee_id"]))}
            for assignee in assignees
        ],
        "findings": [
            {
                key: value
                for key, value in finding.items()
                if key not in {"code", "level"}
            }
            for finding in option.findings
        ],
        "summary": option.summary,
    }


def read_coordination_context(p: Principal, db: Session, body: DemandIdInput) -> Result:
    row = demand_row(p, db, body.demand_id)
    ids: set[str] = set()
    result: dict[str, Any] = {"demand": _demand_view(p, db, row.id, on_read=ids.update)}
    if represents_department(p, row.target_department_id):
        case_id = _case_id(p, db, row.id)
        if case_id:
            case = service.get_case(p, db, case_id)
            ids.update(str(person.employee_id) for person in case.people)
            result["latest_case"] = {
                "case_id": str(case.id),
                "stale": case.stale,
                "options": [
                    {
                        "id": str(o.id),
                        "kind": o.kind_label,
                        "verdict": o.verdict_label,
                        "summary": o.summary,
                    }
                    for o in case.options
                ],
            }
    return Result(result).with_reads(ids)


def inspect_capacity(p: Principal, db: Session, body: DemandIdInput) -> Result:
    row = demand_row(p, db, body.demand_id)
    if not represents_department(p, row.target_department_id):
        return Result({"capacity_reference": _department_view(p, db, row)}).with_reads(
            set()
        )
    data, _, _ = snapshot.build(db, p, row)
    basis = get_demand_basis(p, db, row.id)
    names = {str(person.employee_id): person.display_name for person in basis.data}
    names.update(
        {str(person.employee_id): person.display_name for person in basis.others}
    )
    return Result(
        {
            "people": [
                {
                    "employee_id": person["employee_id"],
                    "name": names.get(person["employee_id"]),
                    "weeks": _weeks(person),
                }
                for person in data["candidates"]
            ]
        }
    ).with_reads({person["employee_id"] for person in data["candidates"]})


def search_qualified_candidates(
    p: Principal, db: Session, body: DemandIdInput
) -> Result:
    row = demand_row(p, db, body.demand_id)
    if not represents_department(p, row.target_department_id):
        return Result({"capacity_reference": _department_view(p, db, row)}).with_reads(
            set()
        )
    data, _, _ = snapshot.build(db, p, row)
    basis = get_demand_basis(p, db, row.id)
    names = {str(person.employee_id): person.display_name for person in basis.data}
    names.update(
        {str(person.employee_id): person.display_name for person in basis.others}
    )
    levels = {str(person.employee_id): person.level_name for person in basis.data}
    return Result(
        {
            "people": [
                {
                    "employee_id": person["employee_id"],
                    "name": names.get(person["employee_id"]),
                    "level": levels.get(person["employee_id"]) or "未定级",
                    "basis": "在需求依据中" if person["in_basis"] else "不在依据中",
                    "newcomer": bool(
                        person["newcomer"] and levels.get(person["employee_id"])
                    ),
                    "weeks": _weeks(person),
                }
                for person in data["candidates"]
            ]
        }
    ).with_reads({person["employee_id"] for person in data["candidates"]})


def simulate_plan(p: Principal, db: Session, body: SimulateInput) -> Result:
    row = demand_row(p, db, body.demand_id)
    if not represents_department(p, row.target_department_id):
        return Result({"error": "无权查看方案"})
    if body.assignees is None:
        case = service.create_case(p, db, row.id)
        return Result(
            {
                "case_id": str(case.id),
                "stale": case.stale,
                "people": [person.model_dump(mode="json") for person in case.people],
                "options": [_option(o, case) for o in case.options],
                "notes": case.notes,
            }
        ).with_reads({str(person.employee_id) for person in case.people})
    case_id = _case_id(p, db, row.id)
    if case_id is None:
        raise service.PlanningNotFound()
    access = service.authorized_case(p, db, case_id)
    option = service.create_option(p, db, access, OptionInput(assignees=body.assignees))
    case = service.get_case(p, db, case_id)
    return Result(
        {"case_id": str(case_id), "option": _option(option, case)}
    ).with_reads({str(person.employee_id) for person in case.people})


def compare_plan_options(p: Principal, db: Session, body: CompareInput) -> Result:
    case = service.get_case(p, db, body.case_id)
    selected = {o.id: o for o in case.options if o.id in body.option_ids}
    if len(selected) != len(set(body.option_ids)):
        raise service.PlanningNotFound()
    return Result(
        {
            "case_id": str(case.id),
            "stale": case.stale,
            "options": [
                _option(selected[option_id], case) for option_id in body.option_ids
            ],
        }
    ).with_reads({str(person.employee_id) for person in case.people})


def get_approval_status(p: Principal, db: Session, body: DemandIdInput) -> Result:
    row = demand_row(p, db, body.demand_id)
    ids: set[str] = set()
    detail = _demand_view(p, db, row.id, on_read=ids.update)
    return Result(
        {
            "status": detail["status"],
            "revision": detail["revision"],
            "events": detail["events"],
            **{
                key: detail[key]
                for key in ("reply_notice", "demand_url")
                if key in detail
            },
        }
    ).with_reads(ids)


COORDINATION_TOOLS = (
    EmployeeTool(
        "read_coordination_context",
        "查看需求、事件时间线和有权看的最新方案摘要",
        DemandIdInput,
        read_coordination_context,
    ),
    EmployeeTool(
        "inspect_capacity",
        "查看需求窗口内可见的容量；非答复方只见部门级参考",
        DemandIdInput,
        inspect_capacity,
    ),
    EmployeeTool(
        "search_qualified_candidates",
        "查找符合能力条件的候选人与新人；非答复方只见部门级参考",
        DemandIdInput,
        search_qualified_candidates,
    ),
    EmployeeTool(
        "simulate_plan",
        "模拟候选方案或按指定人员校验；方案不是承诺",
        SimulateInput,
        simulate_plan,
        True,
    ),
    EmployeeTool(
        "compare_plan_options",
        "并列比较已保存方案；方案不是承诺",
        CompareInput,
        compare_plan_options,
    ),
    EmployeeTool(
        "get_approval_status",
        "查看有权读取的需求答复及审批进度",
        DemandIdInput,
        get_approval_status,
    ),
)
