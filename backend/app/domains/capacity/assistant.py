import re
from collections.abc import Callable
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import BaseModel, Field, JsonValue, ValidationError
from sqlmodel import Session, col, select

from app.authz.principal import Principal
from app.domains.bot import schemas as s
from app.domains.bot.models import BotSession
from app.domains.capability.service import get_schema, list_capabilities
from app.domains.capacity import demand, proposal
from app.domains.capacity.assignment import assignment_rows
from app.domains.capacity.demand_schemas import (
    LIMITED_REFERENCE_TEXT,
    CountCapacityReference,
    DemandInput,
    LimitedCapacityReference,
    PlanReplyDemandInput,
    ReplyInput,
    RespondDemandInput,
)
from app.domains.capacity.extension_schemas import RequestExtensionInput
from app.domains.capacity.models import (
    AssignmentStatus,
    CapacityExtensionRequest,
    CapacityRequestStatus,
    DemandOutcome,
    DemandStatus,
    ProposalStatus,
)
from app.domains.capacity.schemas import REASON_LABELS, WorkloadRequestInput
from app.domains.capacity.settings import (
    CapacityConflict,
    CapacityForbidden,
    CapacityNotFound,
    read_settings,
    require_enabled,
)
from app.domains.capacity.tools import CAPACITY_TOOLS
from app.domains.capacity.workload import employee, get_my_workload
from app.domains.change_request.schemas import validation_message
from app.domains.model.pi_client import ToolDefinition
from app.domains.org.models import Department
from app.domains.org.timezone import read_timezone


class ChangeOnWeb(CapacityConflict):
    def __init__(self) -> None:
        super().__init__()
        self.detail = "请在网页发起或确认变更"


READS = {t.name: t for t in CAPACITY_TOOLS if not t.writes}
WRITES = {t.name: t for t in CAPACITY_TOOLS if t.writes}
CardKind = Literal[
    "demand", "demand_response", "demand_reply", "workload_request", "extension_request"
]
PREPARES: dict[str, tuple[CardKind, str, str]] = {
    "prepare_demand": ("demand", "submit_demand", "提交部门需求"),
    "prepare_demand_response": ("demand_response", "respond_demand", "回应部门需求"),
    "prepare_demand_reply": ("demand_reply", "reply_demand", "答复部门需求"),
    "prepare_workload_request": (
        "workload_request",
        "request_workload_change",
        "申请调整负荷",
    ),
    "prepare_extension_request": ("extension_request", "request_extension", "申请延期"),
}
TITLES = {name: t.description for name, t in READS.items()} | {
    name: title for name, (_, _, title) in PREPARES.items()
}
RESPONSES = {"accept": "接受", "follow_up": "追问", "withdraw": "撤回"}
DEMAND_REPLY_REASON_GUIDANCE = (
    "必需能力未知或未确认时，未知不等于不会。暂时不能承接时须用 reason_code=other（其他）"
    "并说明待核实的能力。reason_code=skill（能力不匹配）只用于已核实不符合能力要求的情况。"
)
UNVERIFIED_CAPABILITY = re.compile(
    r"(?:能力|技能|等级)[^，。；、\n]{0,16}"
    r"(?:未知|待确认|待核实|未确认|未核实|未验证|证据不足)"
    r"|(?:尚未|还未|无法|待)(?:确认|核实|验证)[^，。；、\n]{0,16}(?:能力|技能|等级)"
)

DemandField = Literal[
    "requesting_department_id",
    "target_department_id",
    "capability_key",
    "minimum_level",
    "headcount",
    "allocation_percent",
    "window_start",
    "window_end",
    "description",
]


class PrepareDemandInput(DemandInput):
    unclear_fields: list[DemandField] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list, max_length=2)


def requester_departments(db: Session, p: Principal) -> list[Department]:
    try:
        demand.require_requester(p, db)
    except HTTPException:
        return []
    query = select(Department).where(Department.org_id == p.org_id)
    if not p.is_org_admin:
        ids = p.managed_department_ids
        if not ids:
            department_id = employee(p, db, p.employee_id).department_id
            ids = frozenset({department_id}) if department_id else frozenset()
        query = query.where(col(Department.id).in_(ids))
    return list(db.exec(query.order_by(Department.name, col(Department.id))).all())


class DemandDepartmentForbidden(CapacityForbidden):
    def __init__(self, departments: list[Department]) -> None:
        super().__init__()
        self.detail = "需求方部门只能选择：" + "、".join(d.name for d in departments)


def definitions(db: Session, p: Principal) -> list[ToolDefinition]:
    if not read_settings(p, db).enabled:
        return []
    allowed = True
    try:
        demand.require_requester(p, db)
    except HTTPException:
        allowed = False
    return [
        ToolDefinition(
            name=name,
            description=TITLES[name]
            + ("；只出预览卡，用户点击确认才写入" if name in PREPARES else "")
            + (
                "。" + DEMAND_REPLY_REASON_GUIDANCE
                if name == "prepare_demand_reply"
                else ""
            ),
            parameters=(
                PrepareDemandInput
                if name == "prepare_demand"
                else WRITES[PREPARES[name][1]].input_model
                if name in PREPARES
                else READS[name].input_model
            ).model_json_schema(),
        )
        for name in TITLES
        if (
            allowed
            or name
            not in {"prepare_demand", "capacity_reference", "list_capacity_departments"}
        )
        and (
            p.is_org_admin
            or p.managed_department_ids
            or name not in {"get_demand_basis", "prepare_demand_reply"}
        )
        and (
            p.employee_id is not None
            or name not in {"prepare_workload_request", "prepare_extension_request"}
        )
    ]


def check_demand(
    db: Session, p: Principal, value: BaseModel, unclear: set[str]
) -> tuple[str, list[s.CardField]]:
    body = DemandInput.model_validate(value)
    demand.require_requester(p, db)
    source = demand.department(db, p.org_id, body.requesting_department_id)
    departments = requester_departments(db, p)
    if source.id not in {d.id for d in departments}:
        raise DemandDepartmentForbidden(departments)
    reference = demand.capacity_reference(
        p, db, body.target_department_id, body.capability_key, body.minimum_level
    )
    dictionary = s.Dictionary.model_validate(
        {"data": list_capabilities(db, p.org_id, get_schema(db, p.org_id).id)}
    )
    level = next(
        (
            level.label
            for c in dictionary.data
            if c.key == body.capability_key and c.level_rubric
            for level in c.level_rubric.levels
            if level.key == body.minimum_level
        ),
        "未定级",
    )
    reference_text = (
        LIMITED_REFERENCE_TEXT
        if isinstance(reference, LimitedCapacityReference)
        else f"即时参考：符合要求 {reference.count} 人（各人负荷只有对方部门经理能看到）"
        if isinstance(reference, CountCapacityReference)
        else (
            f"即时参考：符合要求 {reference.count} 人，有余量 {reference.available} 人，"
            f"基本满 {reference.full} 人，超负荷 {reference.overloaded} 人，"
            f"未确认 {reference.unconfirmed} 人"
        )
    )
    content = (
        f"需求方：{source.name}\n目标部门：{reference.department_name}\n"
        f"能力：{reference.capability_name}\n最低等级：{level}\n"
        f"人数：{body.headcount}\n每人投入：{body.allocation_percent}%\n"
        f"时间：{body.window_start} 至 {body.window_end}\n说明：{body.description}\n"
        f"{reference_text}。参考不是承诺，以对方部门经理正式答复为准。"
    )
    values = (
        ("requesting_department_id", "需求方", source.name),
        ("target_department_id", "目标部门", reference.department_name),
        ("capability_key", "能力", reference.capability_name),
        ("minimum_level", "最低等级", level),
        ("headcount", "人数", str(body.headcount)),
        ("allocation_percent", "每人投入", f"{body.allocation_percent}%"),
        ("window_start", "开始日期", str(body.window_start)),
        ("window_end", "结束日期", str(body.window_end)),
        ("description", "说明", body.description),
    )
    return content, [
        s.CardField(label=label, value=value, unclear=key in unclear)
        for key, label, value in values
    ]


def check_demand_response(
    db: Session, p: Principal, value: BaseModel
) -> tuple[str, s.CardLink | None]:
    body = RespondDemandInput.model_validate(value)
    row = demand.demand_row(p, db, body.demand_id)
    if p.user_id is None or not (
        row.requester_user_id == p.user_id
        or row.requesting_department_id in p.managed_department_ids
    ):
        raise CapacityNotFound(demand.DEMAND_NOT_FOUND)
    if (
        row.status == DemandStatus.ACCEPTED
        and proposal.latest(db, p.org_id, row.id, status=ProposalStatus.COMMITTED)
        is not None
    ):
        raise ChangeOnWeb()
    if row.revision != body.revision or row.status in (
        DemandStatus.ACCEPTED,
        DemandStatus.WITHDRAWN,
    ):
        raise CapacityConflict()
    if body.action != "withdraw" and row.status != DemandStatus.REPLIED:
        raise CapacityConflict()
    detail = demand.get_demand(p, db, row.id)
    if body.action == "accept":
        reply = next((e for e in reversed(detail.events) if e.kind == "replied"), None)
        if reply is None or reply.outcome == DemandOutcome.NOT_NOW:
            raise CapacityConflict()
    pending = detail.proposal
    proposal_text = ""
    detail_link = None
    if pending is not None and pending.status == "pending":
        if body.action == "follow_up":
            demand.require_rejection_reason(body.reason_code, body.text)
        if body.action == "follow_up" and (not body.text or len(body.text) > 500):
            raise proposal.ProposalConflict("拒绝方案时必须填写不超过 500 字的理由")
        detail_link = s.CardLink(
            label="查看需求详情", href=f"/capacity?tab=outgoing&demand={row.id}"
        )
        timezone = ZoneInfo(read_timezone(db, p.org_id).timezone)
        proposal_text = (
            f"\n方案人数：{pending.headcount}\n投入："
            + "、".join(f"{percent}%" for percent in pending.allocation_percents)
            + f"\n开始日期：{pending.start_date}\n结束日期：{pending.end_date}"
            + "\n完整条件请前往需求详情查看"
            + f"\n暂留截止：{pending.hold_expires_at.astimezone(timezone):%Y-%m-%d %H:%M}"
            + (
                "\n确认后记录需求方接受，原部门经理和员工本人也同意后才生成分配"
                if body.action == "accept" and pending.borrow_progress
                else "\n确认后对方部门的人员投入会被锁定并生成分配"
                if body.action == "accept"
                else "\n确认后拒绝方案并释放预占"
                if body.action == "follow_up"
                else "\n确认后撤回需求并释放预占"
            )
        )
    rejecting = (
        pending is not None
        and pending.status == "pending"
        and body.action == "follow_up"
    )
    reason = (
        f"拒绝原因：{REASON_LABELS[body.reason_code]}\n"
        if rejecting and body.reason_code is not None
        else ""
    )
    content = (
        f"需求：{detail.capability_name}\n原说明：{detail.description}\n"
        f"操作：{'拒绝方案' if rejecting else RESPONSES[body.action]}\n{reason}"
        f"说明：{body.text or '无'}\n"
        f"修订：{body.revision}{proposal_text}"
    )
    return content, detail_link


def _validate_demand_reply_reason(body: ReplyInput) -> None:
    if body.outcome == DemandOutcome.NOT_NOW:
        demand.require_rejection_reason(body.reason_code, body.text)
        if body.reason_code == "skill" and UNVERIFIED_CAPABILITY.search(body.text):
            raise demand.RejectionReasonInvalid(
                "必需能力尚未确认，请选择「其他」并说明待核实的能力"
            )


def check_demand_reply(db: Session, p: Principal, value: BaseModel) -> str:
    body = PlanReplyDemandInput.model_validate(value)
    row = demand.demand_row(p, db, body.demand_id)
    if not demand.represents_department(p, row.target_department_id):
        raise CapacityNotFound(demand.DEMAND_NOT_FOUND)
    if p.user_id is None or p.user_id == row.requester_user_id:
        raise CapacityForbidden()
    if (
        row.status == DemandStatus.ACCEPTED
        and proposal.latest(db, p.org_id, row.id, status=ProposalStatus.COMMITTED)
        is not None
    ):
        raise ChangeOnWeb()
    if row.revision != body.revision or row.status != DemandStatus.SUBMITTED:
        raise CapacityConflict()
    _validate_demand_reply_reason(body)
    reason = ""
    if body.outcome == DemandOutcome.NOT_NOW and body.reason_code is not None:
        reason = f"暂不能承接的原因：{REASON_LABELS[body.reason_code]}\n"
    description, _, conditions = body.text.partition("\n条件：")
    content = (
        f"需求：{row.capability_name}\n结论："
        f"{ {'can_do': '能做', 'partial': '能做一部分：需要需求方同意下面的条件，或只能承接部分人数、时间、投入', 'not_now': '暂时不能'}[body.outcome] }\n"
        f"承诺日期：{body.start_date or '未定'} 至 {body.end_date or '未定'}\n"
        f"{reason}条件：{conditions.strip() or '无'}\n说明：{description}\n修订：{body.revision}\n"
        "确认后把答复发给需求方，不分配人员，不改其他承诺。"
    )
    if body.lender_department_id is not None:
        lender = demand.department(db, p.org_id, body.lender_department_id)
        content += (
            f"\n借出部门：{lender.name} · 借调 {body.borrow_headcount} 人"
            "\n确认后通知原部门经理选人，选人后才开始预占。"
        )
    elif body.option_id is not None:
        content += (
            "\n确认后把方案发给需求方，并为这些人预占投入 72 小时；需求方接受后才生成分配"
            + ("\n本次明确同意安排需求依据以外的人员" if body.outside_basis else "")
        )
    demand.validate_department_text(p, db, row.target_department_id, content)
    demand.validate_reply_text(p, db, row, body.text)
    return content


def check_workload_request(db: Session, p: Principal, value: BaseModel) -> str:
    from app.domains.capacity.workload import validate_request_week

    body = WorkloadRequestInput.model_validate(value)
    if p.user_id is None:
        raise CapacityForbidden()
    current = get_my_workload(p, db)
    validate_request_week(body.end_week, current.weeks[0].week_start)
    if any(r.status == CapacityRequestStatus.PENDING for r in current.requests):
        raise CapacityConflict()
    return (
        f"申请调整本人负荷\n其他工作占比：{body.requested_percent}%\n"
        f"周范围：{body.start_week} 至 {body.end_week}\n"
        f"原因：{body.reason}\n提交后等待经理审批"
    )


def check_extension_request(db: Session, p: Principal, value: BaseModel) -> str:
    body = RequestExtensionInput.model_validate(value)
    row, _ = assignment_rows(p, db, body.assignment_id)
    if p.user_id is None or row.employee_id != p.employee_id:
        raise CapacityNotFound()
    if row.status != AssignmentStatus.ACTIVE or body.requested_due_date <= row.due_date:
        raise CapacityConflict()
    pending = db.exec(
        select(CapacityExtensionRequest.id).where(
            CapacityExtensionRequest.org_id == p.org_id,
            CapacityExtensionRequest.assignment_id == row.id,
            CapacityExtensionRequest.status == CapacityRequestStatus.PENDING,
        )
    ).first()
    if pending:
        raise CapacityConflict()
    return (
        f"申请延期\n分配编号：{row.id}\n原完成日期：{row.due_date}\n"
        f"申请完成日期：{body.requested_due_date}\n原因：{body.reason}\n提交后等待经理审批"
    )


CHECKS: dict[CardKind, Callable[[Session, Principal, BaseModel], str]] = {
    "demand_reply": check_demand_reply,
    "workload_request": check_workload_request,
    "extension_request": check_extension_request,
}
RESULTS = {
    "demand": "需求已提交",
    "demand_response": "需求回应已提交",
    "demand_reply": "部门答复已发送",
    "workload_request": "负荷调整申请已提交，等待经理审批",
    "extension_request": "延期申请已提交，等待经理审批",
}


def call(
    db: Session,
    p: Principal,
    cards: list[s.Message],
    name: str,
    arguments: dict[str, JsonValue],
) -> JsonValue:
    try:
        require_enabled(p, db)
        if name == "prepare_demand_reply" and arguments.get("option_id") is not None:
            return {"error": "带方案的答复请使用 prepare_plan_proposal 生成确认卡"}
        if name in READS:
            tool = READS[name]
            body = tool.input_model.model_validate(arguments)
            result = tool.handler(p, db, **body.model_dump())
            return result.model_dump(mode="json")
        if name not in PREPARES:
            return {"error": "请使用预览工具，并点击卡片确认"}
        kind, write_name, title = PREPARES[name]
        fields: list[s.CardField] = []
        questions: list[str] = []
        detail_link = None
        if name == "prepare_demand":
            preview = PrepareDemandInput.model_validate(arguments)
            body = DemandInput.model_validate(
                preview.model_dump(exclude={"unclear_fields", "questions"})
            )
            content, fields = check_demand(db, p, body, set(preview.unclear_fields))
            questions = preview.questions
        else:
            body = WRITES[write_name].input_model.model_validate(arguments)
            if kind == "demand_response":
                content, detail_link = check_demand_response(db, p, body)
            else:
                content = CHECKS[kind](db, p, body)
        card = s.CapacityCard(
            kind=kind,
            title=title,
            content=content,
            fields=fields,
            questions=questions,
            arguments=body.model_dump(mode="json"),
            detail_link=detail_link,
        )
        card.actions = [
            s.CapacityAction(action=a, card_id=card.card_id)
            for a in ("confirm_capacity", "cancel_capacity")
        ]
        cards.append(card)
        return {"card_id": str(card.card_id), "preview": content, "status": "pending"}
    except ValidationError as exc:
        return {"error": validation_message(exc, "工具参数不正确")}
    except demand.RejectionReasonInvalid as exc:
        return {
            "error": f"{exc.detail}，请用答复卡重新提交"
            if name == "prepare_demand_reply"
            else exc.detail
        }
    except HTTPException as exc:
        return {"error": exc.detail}


def act(
    db: Session,
    p: Principal,
    session: BotSession,
    cards: list[s.Message],
    action: s.CapacityAction,
) -> s.CapacityCard:
    card = next(
        (
            c
            for c in reversed(cards)
            if isinstance(c, s.CapacityCard) and c.card_id == action.card_id
        ),
        None,
    )
    if card is None or card.status != "pending" or action not in card.actions:
        raise CapacityConflict()
    if card.kind == "demand_reply":
        latest = next(
            (
                c
                for c in reversed(cards)
                if isinstance(c, s.CapacityCard)
                and c.kind == card.kind
                and c.arguments.get("demand_id") == card.arguments.get("demand_id")
            ),
            None,
        )
        if latest is None or latest.card_id != card.card_id:
            raise CapacityConflict()
    card = card.model_copy(deep=True)
    card.actions = []
    if action.action == "cancel_capacity":
        card.status, card.result = "cancelled", "已取消，未提交"
        return card
    try:
        require_enabled(p, db)
        name = next(write for kind, write, _ in PREPARES.values() if kind == card.kind)
        try:
            plan_reply = card.kind == "demand_reply" and card.arguments.get("option_id")
            input_model = (
                PlanReplyDemandInput if plan_reply else WRITES[name].input_model
            )
            body = input_model.model_validate(card.arguments)
        except ValidationError:
            card.status, card.result = "failed", "申请内容已变化，请重新生成确认卡"
            return card
        if card.kind == "demand_reply" and isinstance(body, ReplyInput):
            _validate_demand_reply_reason(body)
        with db.begin_nested():
            try:
                result: BaseModel
                if card.kind == "demand_reply" and card.arguments.get("option_id"):
                    # Planning/Capacity composition belongs to the Agent entrypoint.
                    from app.domains.bot.tools import confirm_plan_reply

                    result = confirm_plan_reply(
                        p,
                        db,
                        PlanReplyDemandInput.model_validate(body),
                        via="feishu" if session.channel == "feishu" else "bot",
                        client_name=session.channel,
                    )
                else:
                    result = WRITES[name].handler(
                        p,
                        db,
                        **body.model_dump(),
                        via="feishu" if session.channel == "feishu" else "bot",
                        client_name=session.channel,
                    )
            except proposal.ProposalFailed as exc:
                card.status, card.result = (
                    "failed",
                    str(
                        exc.detail.get("message", exc.detail)
                        if isinstance(exc.detail, dict)
                        else exc.detail
                    ),
                )
                return card
        card.status = "confirmed"
        card.result = (
            f"{RESULTS[card.kind]}，编号：{result.model_dump(mode='json')['id']}"
        )
    except demand.RejectionReasonInvalid as exc:
        card.status, card.result = "failed", f"{exc.detail}，请重新出卡"
    except HTTPException as exc:
        card.status, card.result = (
            "failed",
            str(
                exc.detail.get("message", exc.detail)
                if isinstance(exc.detail, dict)
                else exc.detail
            ),
        )
    return card
