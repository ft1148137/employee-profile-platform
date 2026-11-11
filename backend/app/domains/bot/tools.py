import re
from uuid import UUID

from pydantic import JsonValue, ValidationError
from sqlmodel import Session

from app.authz.principal import Principal
from app.domains.bot import reviews
from app.domains.bot import schemas as s
from app.domains.bot.models import BotSession
from app.domains.change_request import employee
from app.domains.drafting.settings import drafting_scope, read_auto_draft
from app.domains.employee_tools import EMPLOYEE_TOOLS
from app.domains.model.pi_client import ToolDefinition

TOOLS = {t.name: t for t in EMPLOYEE_TOOLS if t.bot_allowed}
TITLES = {t.name: t.description for t in TOOLS.values()} | {
    "list_pending_reviews": "查看待审批请求",
    "get_review": "查看审批详情",
    "prepare_review_decision": "预览审批决定",
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


def definitions() -> list[ToolDefinition]:
    return reviews.definitions() + [
        ToolDefinition(
            name=t.name,
            description=t.description,
            parameters=t.input_model.model_json_schema(),
        )
        for t in TOOLS.values()
    ]


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


def preview_card(db: Session, p: Principal, request_id: UUID) -> s.PreviewCard:
    preview = s.Preview.model_validate(employee.preview(p, db, request_id))
    return s.PreviewCard(
        **preview.model_dump(),
        actions=[
            s.RequestAction(
                action=a, request_id=preview.request_id, revision=preview.revision
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


def call(
    db: Session,
    p: Principal,
    session: BotSession,
    cards: list[s.Message],
    name: str,
    arguments: dict[str, JsonValue],
) -> JsonValue:
    if name in reviews.INPUTS:
        return redact(reviews.call(db, p, session, cards, name, arguments))
    tool = TOOLS.get(name)
    if tool is None:
        return {"error": "不允许调用此工具，请使用卡片动作"}
    try:
        body = tool.input_model.model_validate(arguments)
    except ValidationError:
        return {"error": "工具参数不正确"}
    kwargs = dict(body)
    if name == "draft_update_request":
        kwargs.update(via="bot", client_name="内置机器人")
    result = PROJECTIONS[name].model_validate(tool.handler(p, db, **kwargs))
    if isinstance(result, s.Profile):
        auto = read_auto_draft(db, p)
        result.auto_draft_enabled = auto.enabled and not auto.department_disabled
    if isinstance(result, s.Dictionary):
        excluded = drafting_scope(db, p.org_id).excluded_capability_keys
        for cap in result.data:
            cap.proactive = cap.key not in excluded
    if isinstance(result, (s.Request, s.Preview)):
        request_id = result.id if isinstance(result, s.Request) else result.request_id
        if request_id not in session.request_ids:
            session.request_ids = [*session.request_ids, request_id]
            db.add(session)
        cards.append(preview_card(db, p, request_id))
    if isinstance(result, s.Requests):
        for request in result.data:
            cards.append(
                preview_card(db, p, request.id)
                if request.status in ("draft", "pending_owner")
                else status_card(request)
            )
    if isinstance(result, s.Questions):
        cards.extend(question_card(q) for q in result.data)
    if isinstance(result, s.Question):
        cards.append(question_card(result))
    return redact(result.model_dump(mode="json"))
