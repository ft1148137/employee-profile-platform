from typing import Literal

from fastapi import HTTPException
from pydantic import JsonValue, ValidationError
from sqlmodel import Session

from app.authz.principal import Principal
from app.domains.bot import schemas as s
from app.domains.bot.models import BotSession
from app.domains.capability.service import get_schema, list_capabilities
from app.domains.capacity import demand
from app.domains.capacity.demand_schemas import (
    DemandInput,
    DemandList,
    DemandPublic,
    RespondDemandInput,
)
from app.domains.capacity.models import DemandOutcome, DemandStatus
from app.domains.capacity.settings import (
    CapacityConflict,
    CapacityForbidden,
    CapacityNotFound,
    read_settings,
    require_enabled,
)
from app.domains.capacity.tools import CAPACITY_TOOLS
from app.domains.capacity.workload import employee
from app.domains.change_request.schemas import validation_message
from app.domains.model.pi_client import ToolDefinition

READS = {t.name: t for t in CAPACITY_TOOLS if not t.writes}
WRITES = {t.name: t for t in CAPACITY_TOOLS if t.writes}
PREPARES: dict[str, tuple[Literal["demand", "demand_response"], str, str]] = {
    "prepare_demand": ("demand", "submit_demand", "提交部门需求"),
    "prepare_demand_response": ("demand_response", "respond_demand", "回应部门需求"),
}
TITLES = {name: t.description for name, t in READS.items()} | {
    name: title for name, (_, _, title) in PREPARES.items()
}
RESPONSES = {"accept": "接受", "follow_up": "追问", "withdraw": "撤回"}


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
            + ("；只出预览卡，用户点击确认才写入" if name in PREPARES else ""),
            parameters=(
                WRITES[PREPARES[name][1]].input_model
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
        and (p.is_org_admin or p.managed_department_ids or name != "get_demand_basis")
    ]


def check_demand(db: Session, p: Principal, body: DemandInput) -> str:
    demand.require_requester(p, db)
    source = demand.department(db, p.org_id, body.requesting_department_id)
    if not p.is_org_admin:
        allowed = (
            source.id in p.managed_department_ids
            if p.managed_department_ids
            else employee(p, db, p.employee_id).department_id == source.id
        )
        if not allowed:
            raise CapacityForbidden()
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
    return (
        f"需求方：{source.name}\n目标部门：{reference.department_name}\n"
        f"能力：{reference.capability_name}\n最低等级：{level}\n"
        f"人数：{body.headcount}\n每人投入：{body.allocation_percent}%\n"
        f"时间：{body.window_start} 至 {body.window_end}\n说明：{body.description}\n"
        f"即时参考：符合要求 {reference.count} 人，有余量 {reference.available} 人，"
        f"基本满 {reference.full} 人，超负荷 {reference.overloaded} 人，"
        f"未确认 {reference.unconfirmed} 人。参考不是承诺，以对方部门经理正式答复为准。"
    )


def check_demand_response(db: Session, p: Principal, body: RespondDemandInput) -> str:
    row = demand.demand_row(p, db, body.demand_id)
    if p.user_id is None or not (
        row.requester_user_id == p.user_id
        or row.requesting_department_id in p.managed_department_ids
    ):
        raise CapacityNotFound("未找到该需求")
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
    return (
        f"需求：{detail.capability_name}\n原说明：{detail.description}\n"
        f"操作：{RESPONSES[body.action]}\n说明：{body.text or '无'}\n"
        f"修订：{body.revision}"
    )


def call(
    db: Session,
    p: Principal,
    cards: list[s.Message],
    name: str,
    arguments: dict[str, JsonValue],
) -> JsonValue:
    try:
        require_enabled(p, db)
        if name in READS:
            tool = READS[name]
            body = tool.input_model.model_validate(arguments)
            result = tool.handler(p, db, **body.model_dump())
            rows = result.data if isinstance(result, DemandList) else [result]
            for row in rows:
                if isinstance(row, DemandPublic):
                    for event in row.events:
                        event.responder_name = None
            return result.model_dump(mode="json")
        if name not in PREPARES:
            return {"error": "请使用预览工具，并点击卡片确认"}
        kind, write_name, title = PREPARES[name]
        body = WRITES[write_name].input_model.model_validate(arguments)
        if isinstance(body, DemandInput):
            content = check_demand(db, p, body)
        else:
            assert isinstance(body, RespondDemandInput)
            content = check_demand_response(db, p, body)
        card = s.CapacityCard(
            kind=kind,
            title=title,
            content=content,
            arguments=body.model_dump(mode="json"),
        )
        card.actions = [
            s.CapacityAction(action=a, card_id=card.card_id)
            for a in ("confirm_capacity", "cancel_capacity")
        ]
        cards.append(card)
        return {"card_id": str(card.card_id), "preview": content, "status": "pending"}
    except ValidationError as exc:
        return {"error": validation_message(exc, "工具参数不正确")}
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
    card = card.model_copy(deep=True)
    card.actions = []
    if action.action == "cancel_capacity":
        card.status, card.result = "cancelled", "已取消，未提交"
        return card
    try:
        require_enabled(p, db)
        name = next(write for kind, write, _ in PREPARES.values() if kind == card.kind)
        body = WRITES[name].input_model.model_validate(card.arguments)
        result = WRITES[name].handler(
            p,
            db,
            **body.model_dump(),
            via="feishu" if session.channel == "feishu" else "bot",
            client_name=session.channel,
        )
        card.status = "confirmed"
        card.result = f"已提交，需求编号：{result.model_dump(mode='json')['id']}"
    except HTTPException as exc:
        card.status, card.result = "failed", str(exc.detail)
    return card
