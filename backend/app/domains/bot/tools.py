import json
import re
from collections.abc import Callable
from dataclasses import replace
from typing import Literal, cast
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import BaseModel, Field, JsonValue, ValidationError
from sqlmodel import Session, col, select

from app.authz.principal import Principal
from app.domains.bot import attachments, reviews
from app.domains.bot import schemas as s
from app.domains.bot.levels import name_levels
from app.domains.bot.models import BotSession
from app.domains.capability.proposals import suggest_capability
from app.domains.capability.schemas import SuggestCapability
from app.domains.capability.search import PeopleSearchInput, find_people
from app.domains.capacity import assistant as capacity
from app.domains.capacity import demand as capacity_demand
from app.domains.capacity.settings import read_settings as read_capacity_settings
from app.domains.change_request import employee
from app.domains.change_request.models import RequestStatus
from app.domains.change_request.schemas import ItemInput, ToolInput, validation_message
from app.domains.drafting.settings import read_auto_draft
from app.domains.employee_tools import EMPLOYEE_TOOLS, DraftRequestInput
from app.domains.evidence.models import Evidence
from app.domains.model.client import ModelUnavailable
from app.domains.model.pi_client import ToolDefinition
from app.domains.org.models import Department, Employee, EmploymentStatus
from app.domains.planning import service as planning_service
from app.domains.planning.schemas import VERDICT_LABELS, CasePublic, OptionPublic
from app.domains.planning.tools import COORDINATION_TOOLS
from app.domains.planning.tools import Result as CoordinationResult
from app.domains.profile.service import read_profile
from app.domains.review import direct

TOOLS = {t.name: t for t in EMPLOYEE_TOOLS if t.bot_allowed}
COORDINATION = {t.name: t for t in COORDINATION_TOOLS}
TITLES = {t.name: t.description for t in TOOLS.values()} | {
    "read_attachment": "读取会话附件",
    "suggest_capability": "建议新增能力",
    "list_team_members": "查看团队成员",
    "find_people": "按能力找人",
    "get_member_profile": "查看下属画像",
    "prepare_profile_edit": "预览直接修改画像",
    "list_pending_reviews": "查看待审批请求",
    "get_review": "查看审批详情",
    "prepare_review_decision": "预览审批决定",
}
COORDINATION_TITLES = {
    "coordination": "调查并模拟部门协调方案",
    "prepare_plan_proposal": "预览部门方案答复",
}
PROJECTIONS: dict[str, type[s.Projection]] = {
    "get_my_profile": s.Profile,
    "list_my_evidence": s.EvidenceList,
    "get_my_requests": s.Requests,
    "get_capability_dictionary": s.Dictionary,
    "draft_update_request": s.Request,
    "preview_request": s.Preview,
    "get_pending_questions": s.Questions,
    "answer_question": s.Question,
}


class TeamListInput(ToolInput):
    q: str | None = Field(default=None, max_length=100)
    department_id: UUID | None = None


class MemberInput(ToolInput):
    employee_id: UUID


class ProfileEditInput(direct.DirectEditInput):
    employee_id: UUID


class CoordinationInput(BaseModel):
    function: Literal[
        "read_coordination_context",
        "inspect_capacity",
        "search_qualified_candidates",
        "simulate_plan",
        "compare_plan_options",
        "get_approval_status",
    ]
    arguments: dict[str, JsonValue]


class PreparePlanProposalInput(BaseModel):
    case_id: UUID
    option_id: UUID
    text: str = Field(min_length=1, max_length=2000)


TEAM_INPUTS: dict[str, type[BaseModel]] = {
    "find_people": PeopleSearchInput,
    "list_team_members": TeamListInput,
    "get_member_profile": MemberInput,
    "prepare_profile_edit": ProfileEditInput,
}


class TeamMemberNotFound(HTTPException):
    def __init__(self) -> None:
        super().__init__(404, "找不到该员工")


def team_member(db: Session, p: Principal, employee_id: UUID) -> Employee:
    subject = db.exec(
        select(Employee).where(
            Employee.org_id == p.org_id,
            Employee.id == employee_id,
            col(Employee.department_id).in_(p.managed_department_ids),
            Employee.employment_status == EmploymentStatus.ACTIVE,
        )
    ).first()
    if subject is None:
        raise TeamMemberNotFound()
    return subject


def team_call(
    db: Session,
    p: Principal,
    cards: list[s.Message],
    name: str,
    arguments: dict[str, JsonValue],
    *,
    on_read: Callable[[set[str]], None] | None = None,
) -> JsonValue:
    if not p.managed_department_ids:
        return {"error": "不允许调用此工具"}
    p = replace(p, is_org_admin=False)
    try:
        body = TEAM_INPUTS[name].model_validate(arguments)
    except ValidationError as exc:
        return {"error": validation_message(exc, "工具参数不正确")}
    if isinstance(body, PeopleSearchInput):
        result = find_people(db, p, body).model_dump(mode="json")
        if on_read:
            on_read({row["employee_id"] for row in result["data"]})
        for row in result["data"]:
            row["profile_url"] = f"/employees/{row['employee_id']}"
        return result
    if isinstance(body, TeamListInput):
        query = (
            select(Employee, Department)
            .join(
                Department,
                (col(Department.org_id) == Employee.org_id)
                & (col(Department.id) == Employee.department_id),
            )
            .where(
                Employee.org_id == p.org_id,
                col(Employee.department_id).in_(p.managed_department_ids),
                Employee.employment_status == EmploymentStatus.ACTIVE,
            )
        )
        if body.q:
            query = query.where(
                col(Employee.display_name).icontains(body.q, autoescape=True)
            )
        if body.department_id:
            query = query.where(Employee.department_id == body.department_id)
        rows = db.exec(query.order_by(col(Employee.id)).limit(100)).all()
        if on_read:
            on_read({str(e.id) for e, _ in rows})
        return {
            "data": [
                {
                    "employee_id": str(e.id),
                    "name": e.display_name,
                    "department": d.name,
                    "title": e.title,
                }
                for e, d in rows
            ]
        }
    assert isinstance(body, (MemberInput, ProfileEditInput))
    subject = team_member(db, p, body.employee_id)
    if on_read:
        on_read({str(subject.id)})
    if isinstance(body, ProfileEditInput):
        edit = direct.DirectEditInput(
            items=body.items, base_revision=body.base_revision
        )
        preview = direct.preview_direct_edit(p, db, subject.id, edit)
        card = s.ProfileEditCard(
            **preview.model_dump(),
            employee_id=subject.id,
            employee_name=subject.display_name,
            edit=edit,
        )
        card.actions = [
            s.ProfileEditAction(action=a, card_id=card.card_id)
            for a in ("confirm_profile_edit", "continue_profile_edit")
        ]
        card = name_levels(db, p, card)
        cards.append(card)
        return {
            "card_id": str(card.card_id),
            "items": [i.model_dump(mode="json") for i in card.items],
            "revision": card.revision,
        }
    profile = read_profile(db, p, subject.id)
    projected = name_levels(db, p, s.Profile.model_validate(profile))
    return {
        "employee_id": str(subject.id),
        "revision": profile.latest_published.revision
        if profile.latest_published
        else None,
        "items": [item.model_dump(mode="json") for item in projected.items],
    }


def definitions(
    p: Principal | None = None, *, db: Session | None = None, channel: str = "web"
) -> list[ToolDefinition]:
    coordination_enabled = bool(
        db is not None
        and p
        and channel in ("web", "feishu")
        and read_capacity_settings(p, db).enabled
    )
    team = (
        [
            ToolDefinition(
                name=name,
                description=TITLES[name]
                + "；仅限管辖部门。"
                + (
                    "只校验，不写入；必须点击确认修改才发布并通知员工。"
                    if name == "prepare_profile_edit"
                    else ""
                ),
                parameters=model.model_json_schema(),
            )
            for name, model in TEAM_INPUTS.items()
        ]
        if p and p.managed_department_ids
        else []
    )
    return (
        (
            [
                tool
                for tool in capacity.definitions(db, p)
                if not coordination_enabled
                or tool.name not in {"get_demand", "get_demand_basis"}
            ]
            if db is not None and p and channel in ("web", "feishu")
            else []
        )
        + team
        + (
            [
                ToolDefinition(
                    name="coordination",
                    description=(
                        "按当前用户权限调查、模拟和比较部门方案；方案不是承诺。"
                        "simulate_plan 首次模拟只传 demand_id，生成快照和候选方案；"
                        "已有首次模拟后，传 demand_id 和 assignees 校验自定方案。"
                        "各 function 的说明及 arguments JSON Schema 如下（required 为必填字段）：\n"
                        + json.dumps(
                            {
                                tool.name: {
                                    "description": tool.description,
                                    "parameters": tool.input_model.model_json_schema(),
                                }
                                for tool in COORDINATION_TOOLS
                            },
                            ensure_ascii=False,
                        )
                    ),
                    parameters=CoordinationInput.model_json_schema(),
                )
            ]
            if coordination_enabled
            else []
        )
        + (
            [
                ToolDefinition(
                    name="prepare_plan_proposal",
                    description=(
                        "只为答复方生成需求答复确认卡；经理点击确认才发送承诺。"
                        "说明与系统追加的完整方案条件合计最多 2000 字；"
                        "超限时按工具提示缩短说明后重新预览，不删减方案条件。"
                    ),
                    parameters=PreparePlanProposalInput.model_json_schema(),
                )
            ]
            if coordination_enabled
            and p
            and (p.is_org_admin or p.managed_department_ids)
            else []
        )
        + [
            attachments.READ_TOOL,
            ToolDefinition(
                name="suggest_capability",
                description=TITLES["suggest_capability"]
                + "；先复述名称和说明，员工明确同意后才调用；只建议，不改画像或词典。",
                parameters=SuggestCapability.model_json_schema(),
            ),
        ]
        + reviews.definitions()
        + [
            ToolDefinition(
                name=t.name,
                description=t.description,
                parameters=t.input_model.model_json_schema(),
            )
            for t in TOOLS.values()
        ]
    )


def redact_text(value: str) -> str:
    return re.sub(
        r"https?://[^\s<>]+|[\w.+-]+@[\w.-]+", "[已隐藏]", value, flags=re.IGNORECASE
    )


def redact(value: JsonValue) -> JsonValue:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    return value


VERDICT_ORDER = {
    "feasible": 0,
    "conditional": 1,
    "insufficient_data": 2,
    "infeasible": 3,
}


def plan_metrics(option: OptionPublic) -> list[s.CardField]:
    summary = option.summary
    return [
        s.CardField(label="类型", value=option.kind_label),
        s.CardField(label="结论", value=option.verdict_label),
        s.CardField(label="交付日期", value=str(summary.get("finish_date") or "未定")),
        s.CardField(
            label="覆盖人数", value=f"{summary.get('covered_headcount', 0)} 人"
        ),
        s.CardField(label="缺口", value=f"{summary.get('gap_person_weeks', 0)} 人周"),
        s.CardField(label="新人数", value=f"{summary.get('newcomer_count', 0)} 人"),
    ]


def plan_card(option: OptionPublic, case: CasePublic) -> s.PlanCard:
    names = {str(person.employee_id): person.display_name for person in case.people}
    assignees = cast("list[dict[str, JsonValue]]", option.params.get("assignees", []))
    summary = option.summary
    affected = cast("list[str]", summary.get("affected_assignment_ids", []))
    findings = option.findings
    people = (
        "、".join(
            names.get(str(person.get("employee_id")), "待确认人员")
            for person in assignees
        )
        or "尚未确定具体人员"
    )
    risks = (
        "；".join(str(item.get("message", "")) for item in findings) or "暂无已发现风险"
    )
    approvers = cast("list[str]", summary.get("approvers", []))
    metrics = plan_metrics(option)
    fields = [
        *metrics[:5],
        s.CardField(label="人员", value=people),
        s.CardField(label="受影响的工作数", value=f"{len(affected)} 项"),
        s.CardField(label="风险", value=risks),
        s.CardField(label="审批人", value="、".join(approvers) or "未定"),
    ]
    changes = None
    if isinstance(summary.get("diff"), dict):
        diff = cast("dict[str, dict[str, JsonValue]]", summary["diff"])
        parts = [str(summary.get("changes") or "已修改方案")]
        for key, label in (
            ("verdict", "结论"),
            ("finish_date", "交付日期"),
            ("covered_headcount", "覆盖人数"),
            ("gap_person_weeks", "缺口"),
        ):
            values = diff.get(key)
            if isinstance(values, dict) and values.get("before") != values.get("after"):
                before, after = values.get("before"), values.get("after")
                if key == "verdict":
                    before = next(
                        (
                            label
                            for verdict, label in VERDICT_LABELS.items()
                            if verdict.value == before
                        ),
                        str(before),
                    )
                    after = next(
                        (
                            label
                            for verdict, label in VERDICT_LABELS.items()
                            if verdict.value == after
                        ),
                        str(after),
                    )
                unit = {"covered_headcount": " 人", "gap_person_weeks": " 人周"}.get(
                    key, ""
                )
                before = "未定" if before is None else f"{before}{unit}"
                after = "未定" if after is None else f"{after}{unit}"
                parts.append(f"{label}：{before} → {after}")
        changes = "；".join(parts)
    link = f"/capacity?tab=incoming&demand={case.demand_id}&plans=1"
    best = min(
        (
            o
            for o in case.options
            if o.kind != "custom" and o.snapshot_id == option.snapshot_id
        ),
        key=lambda o: VERDICT_ORDER[o.verdict],
        default=None,
    )
    fallback = None
    if best is not None and VERDICT_ORDER[option.verdict] > VERDICT_ORDER[best.verdict]:
        rule_metrics = plan_metrics(best)
        fields.insert(5, metrics[-1])
        fallback = s.RulePlanFallback(
            fields=rule_metrics,
            differences=[
                s.CardField(label=a.label, value=f"{a.value} → {b.value}")
                for a, b in zip(metrics, rule_metrics, strict=True)
            ],
            link=f"{link}#case={case.id}",
        )
    return s.PlanCard(
        title=f"{option.kind_label}方案",
        fields=fields,
        changes=changes,
        link=link,
        rule_fallback=fallback,
    )


def preview_card(db: Session, p: Principal, request_id: UUID) -> s.PreviewCard:
    preview = name_levels(
        db, p, s.Preview.model_validate(employee.preview(p, db, request_id))
    )
    return s.PreviewCard(
        **preview.model_dump(),
        actions=[
            s.RequestAction(
                action=a,
                request_id=preview.request_id,
                revision=preview.revision,
                base_revision=preview.base_revision,
            )
            for a in ("confirm_submit", "continue_editing")
        ],
    )


def status_card(request: s.Request) -> s.StatusCard:
    return s.StatusCard(
        **request.model_dump(),
        actions=[
            s.RequestAction(
                action="withdraw", request_id=request.id, revision=request.revision
            )
        ]
        if request.status == "pending_review"
        else [],
    )


def question_card(question: s.Question) -> s.QuestionCard:
    return s.QuestionCard(
        **question.model_dump(),
        actions=["answer_question"] if question.status == "open" else [],
    )


def request_card(cards: list[s.Message], card: s.PreviewCard | s.StatusCard) -> None:
    request_id = card.request_id if isinstance(card, s.PreviewCard) else card.id
    cards[:] = [
        existing
        for existing in cards
        if not (
            isinstance(existing, s.PreviewCard)
            and existing.request_id == request_id
            or isinstance(existing, s.StatusCard)
            and existing.id == request_id
        )
    ]
    cards.append(card)


def pending_overlap(
    db: Session,
    p: Principal,
    session: BotSession,
    cards: list[s.Message],
    items: list[ItemInput],
) -> tuple[list[ItemInput], dict[str, JsonValue]]:
    keys = {item.capability_key for item in items}
    blocked: set[str] = set()
    overlaps: list[JsonValue] = []
    for request in employee.my_requests(p, db).data:
        if request.status != RequestStatus.PENDING_REVIEW:
            continue
        matching = [
            i for i in request.items if i.decision is None and i.capability.key in keys
        ]
        if not matching:
            continue
        blocked.update(i.capability.key for i in matching)
        # Withdrawal retracts these reports; resubmission recreates them from the statements.
        reports = set(
            db.exec(
                select(Evidence.id).where(
                    Evidence.org_id == p.org_id,
                    Evidence.subject_employee_id == p.employee_id,
                    col(Evidence.provenance)["request_id"].astext == str(request.id),
                )
            ).all()
        )
        overlaps.append(
            {
                "request_id": str(request.id),
                "capabilities": [i.capability.name for i in matching],
                "submitted_at": request.submitted_at.astimezone(
                    ZoneInfo("Asia/Shanghai")
                ).isoformat()
                if request.submitted_at
                else None,
                "reviewers": [r.model_dump(mode="json") for r in request.reviewers],
                "original_items": [
                    ItemInput(
                        op=i.op,
                        capability_key=i.capability.key,
                        statement=i.after.statement if i.after else "撤回或补充依据",
                        level=i.after.level if i.after else None,
                        owner_statement=i.owner_statement,
                        objection=i.objection,
                        uncertainty=i.uncertainty,
                        evidence_ids=[
                            e.evidence_id
                            for e in i.evidence
                            if e.evidence_id not in reports
                        ],
                    ).model_dump(mode="json")
                    for i in request.items
                    if i.decision is None
                ],
            }
        )
        if request.id not in session.request_ids:
            session.request_ids = [*session.request_ids, request.id]
            db.add(session)
        request_card(
            cards, status_card(name_levels(db, p, s.Request.model_validate(request)))
        )
    notice: dict[str, JsonValue] = {}
    if overlaps:
        notice = {
            "overlaps": overlaps,
            "message": "重叠能力已有待审请求，本次没有为这些能力保存草稿或生成预览。"
            "请说明下面各能力的提交时间（北京时间）和正在等谁审批，并让员工选择："
            "等原请求审完再补充；或者点击撤回卡，撤回原请求后合并重提。"
            "员工明确选择合并重提时，先请其点击撤回卡；确认撤回成功后，"
            "将 original_items 中原请求的全部待审内容与本次补充合并，"
            "复用 draft_update_request 的 items 参数新建草稿，保留原有等级和依据说明，"
            "再请员工核对预览并点击确认提交。其他不重叠的能力照常保存。",
        }
    return [item for item in items if item.capability_key not in blocked], notice


def checked_preview_card(
    db: Session,
    p: Principal,
    session: BotSession,
    cards: list[s.Message],
    request_id: UUID,
) -> dict[str, JsonValue]:
    request = next(
        r for r in employee.my_requests(p, db, "all").data if r.id == request_id
    )
    if request.status in ("draft", "pending_owner"):
        remaining, notice = pending_overlap(
            db,
            p,
            session,
            cards,
            [
                ItemInput(
                    op=i.op,
                    capability_key=i.capability.key,
                    statement=i.after.statement if i.after else "撤回或补充依据",
                )
                for i in request.items
            ],
        )
        if notice:
            keys = {i.capability_key for i in remaining}
            names = "、".join(
                i.capability.name for i in request.items if i.capability.key not in keys
            )
            request_card(
                cards,
                status_card(name_levels(db, p, s.Request.model_validate(request))),
            )
            return {
                "request_id": str(request_id),
                "message": f"这份旧草稿的{names}已有待审请求，暂不提供确认提交预览。"
                "可以等审完再处理，或者在画像页删除这份草稿。",
            }
    request_card(cards, preview_card(db, p, request_id))
    return {}


def call(
    db: Session,
    p: Principal,
    session: BotSession,
    cards: list[s.Message],
    name: str,
    arguments: dict[str, JsonValue],
    *,
    on_read: Callable[[set[str]], None] | None = None,
) -> JsonValue:
    if name in {"coordination", "prepare_plan_proposal"}:
        if (
            session.channel not in ("web", "feishu")
            or not read_capacity_settings(p, db).enabled
        ):
            return {"error": "不允许调用此工具"}
        try:
            if name == "coordination":
                coord_request = CoordinationInput.model_validate(arguments)
                coord_tool = COORDINATION[coord_request.function]
                input_body = coord_tool.input_model.model_validate(
                    coord_request.arguments
                )
                with db.begin_nested():
                    result = coord_tool.handler(p, db, input_body)
                read_ids = (
                    result._read_employee_ids
                    if isinstance(result, CoordinationResult)
                    else None
                )
                if on_read and read_ids is not None:
                    on_read(read_ids)
                data = result.model_dump(mode="json")
                if (
                    coord_request.function in {"simulate_plan", "compare_plan_options"}
                    and "error" not in data
                ):
                    case = planning_service.get_case(p, db, UUID(data["case_id"]))
                    if on_read and read_ids is not None:
                        on_read({str(person.employee_id) for person in case.people})
                    row = capacity_demand.demand_row(p, db, case.demand_id)
                    if not capacity_demand.represents_department(
                        p, row.target_department_id
                    ):
                        return {"error": "无权查看方案"}
                    selected = [
                        UUID(item["id"])
                        for item in data.get("options", [data.get("option")])
                        if item
                    ]
                    new_cards = [
                        plan_card(option, case)
                        for option in case.options
                        if option.id in selected
                    ]
                    cards.extend(new_cards)
                return redact(data)
            proposal = PreparePlanProposalInput.model_validate(arguments)
            case = planning_service.get_case(p, db, proposal.case_id)
            if on_read:
                on_read({str(person.employee_id) for person in case.people})
            row = capacity_demand.demand_row(p, db, case.demand_id)
            if not capacity_demand.represents_department(p, row.target_department_id):
                return {"error": "无权查看方案"}
            option = next((o for o in case.options if o.id == proposal.option_id), None)
            if option is None or case.stale:
                return {"error": "方案已变化，请重新模拟"}
            if any(
                person.display_name and person.display_name in proposal.text
                for person in case.people
            ):
                return {"error": "答复只写部门层面的结论，不写人名"}
            assignees = cast("list[dict[str, str]]", option.params.get("assignees", []))
            start_date = min((a["start_date"] for a in assignees), default=None)
            end_date = max((a["end_date"] for a in assignees), default=None)
            outcome = {
                "feasible": "can_do",
                "conditional": "partial",
                "infeasible": "not_now",
                "insufficient_data": "not_now",
            }[option.verdict]
            conditions = cast("list[str]", option.summary.get("conditions", []))
            condition_text = "\n条件：" + ("；".join(conditions) or "无")
            text = proposal.text + condition_text
            if len(text) > 2000:
                return {
                    "error": (
                        "说明与完整方案条件合计不能超过 2000 字，"
                        f"本次说明最多可写 {2000 - len(condition_text)} 字，"
                        f"当前超出 {len(text) - 2000} 字。"
                        "请缩短说明后重新预览，保留全部方案条件。"
                    )
                }
            reply_result = capacity.call(
                db,
                p,
                cards,
                "prepare_demand_reply",
                {
                    "demand_id": str(row.id),
                    "revision": row.revision,
                    "outcome": outcome,
                    "start_date": start_date,
                    "end_date": end_date,
                    "text": text,
                },
            )
            card = plan_card(option, case)
            if (
                card.rule_fallback is not None
                and isinstance(reply_result, dict)
                and "error" not in reply_result
            ):
                for message in cards:
                    if isinstance(message, s.CapacityCard) and str(
                        message.card_id
                    ) == reply_result.get("card_id"):
                        message.plan = card.model_copy(
                            update={"fields": plan_metrics(option), "changes": None}
                        )
            return redact(reply_result)
        except ValidationError as exc:
            return {"error": validation_message(exc, "工具参数不正确")}
        except HTTPException as exc:
            if 400 <= exc.status_code < 500:
                return {"error": exc.detail}
            raise ModelUnavailable(code="coordination_tool") from None
        except Exception:
            raise ModelUnavailable(code="coordination_tool") from None
    if name in capacity.TITLES or name in capacity.WRITES:
        if session.channel not in ("web", "feishu"):
            return {"error": "不允许调用此工具"}
        return redact(capacity.call(db, p, cards, name, arguments))
    if name == "suggest_capability":
        try:
            suggestion = SuggestCapability.model_validate(arguments)
            return suggest_capability(
                p, db, **suggestion.model_dump(), commit=False
            ).model_dump(mode="json")
        except ValidationError as exc:
            return {"error": validation_message(exc, "工具参数不正确")}
        except HTTPException as exc:
            return {"error": exc.detail}
    if name == "read_attachment":
        try:
            body_attachment = attachments.ReadAttachmentInput.model_validate(arguments)
            return redact(
                attachments.read_attachment(
                    db, p, "bot", session.id, body_attachment.attachment_id
                )
            )
        except ValidationError as exc:
            return {"error": validation_message(exc, "工具参数不正确")}
        except HTTPException:
            return {"error": "附件不存在"}
    if name in TEAM_INPUTS:
        try:
            return redact(team_call(db, p, cards, name, arguments, on_read=on_read))
        except employee.InvalidItems as exc:
            return {"error": "；".join(str(e["message"]) for e in exc.errors)}
    if name in reviews.INPUTS:
        return redact(reviews.call(db, p, session, cards, name, arguments))
    tool = TOOLS.get(name)
    if tool is None:
        return {"error": "不允许调用此工具，请使用卡片动作"}
    try:
        body = tool.input_model.model_validate(arguments)
    except ValidationError as exc:
        return {"error": validation_message(exc, "工具参数不正确")}
    kwargs = dict(body)
    notice: dict[str, JsonValue] = {}
    if name == "draft_update_request":
        assert isinstance(body, DraftRequestInput)
        remaining, notice = pending_overlap(db, p, session, cards, body.items)
        if notice and not remaining:
            return redact(notice)
        kwargs["items"] = remaining
        kwargs.update(via="bot", client_name="内置机器人")
    try:
        result = PROJECTIONS[name].model_validate(tool.handler(p, db, **kwargs))
    except employee.InvalidItems as exc:
        return {"error": "；".join(str(e["message"]) for e in exc.errors)}
    result = name_levels(db, p, result)
    if isinstance(result, s.Profile):
        auto = read_auto_draft(db, p)
        result.auto_draft_enabled = auto.enabled and not auto.org_disabled
    blocked_drafts: list[JsonValue] = []
    if isinstance(result, (s.Request, s.Preview)):
        request_id = result.id if isinstance(result, s.Request) else result.request_id
        if request_id not in session.request_ids:
            session.request_ids = [*session.request_ids, request_id]
            db.add(session)
        blocked = checked_preview_card(db, p, session, cards, request_id)
        if blocked:
            blocked_drafts.append(blocked)
    if isinstance(result, s.Requests):
        for request in result.data:
            if request.id not in session.request_ids:
                session.request_ids = [*session.request_ids, request.id]
                db.add(session)
            if request.status in ("draft", "pending_owner"):
                blocked = checked_preview_card(db, p, session, cards, request.id)
                if blocked:
                    blocked_drafts.append(blocked)
            else:
                request_card(cards, status_card(request))
    if isinstance(result, s.Questions):
        cards.extend(question_card(q) for q in result.data)
    if isinstance(result, s.Question):
        cards.append(question_card(result))
    if blocked_drafts:
        notice["blocked_drafts"] = blocked_drafts
    return redact(result.model_dump(mode="json") | notice)
