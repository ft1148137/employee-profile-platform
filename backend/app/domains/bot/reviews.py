from uuid import UUID

from pydantic import JsonValue, ValidationError
from sqlmodel import Session

from app.authz.principal import Principal
from app.domains import manager_tools
from app.domains.bot import schemas as s
from app.domains.bot.models import BotSession
from app.domains.change_request.employee import EmployeeRecordNotFound, RequestConflict
from app.domains.change_request.schemas import ToolInput
from app.domains.employee_tools import RequestInput
from app.domains.model.pi_client import ToolDefinition
from app.domains.review import service
from app.domains.review.schemas import DecideInput


class PrepareInput(DecideInput):
    request_id: UUID
    # Decisions and their reasons are repeated on the card; no hidden overall comment.
    comment: None = None


INPUTS: dict[str, type[ToolInput]] = {
    "list_pending_reviews": ToolInput,
    "get_review": RequestInput,
    "prepare_review_decision": PrepareInput,
}


def definitions() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name=name,
            description=(
                "查看本人有权审批的请求；不能审批自己的请求。"
                if name != "prepare_review_decision"
                else "仅校验并复述每项决定，不写入。退回和拒绝必须有经理给的原因，"
                "没有就追问；只有经理点击卡片的确认决定才生效。"
            ),
            parameters=model.model_json_schema(),
        )
        for name, model in INPUTS.items()
    ]


def call(
    db: Session,
    p: Principal,
    _session: BotSession,
    cards: list[s.Message],
    name: str,
    arguments: dict[str, JsonValue],
) -> JsonValue:
    try:
        body = INPUTS[name].model_validate(arguments)
    except ValidationError:
        return {"error": "请逐项给出决定，退回和拒绝必须说明原因"}
    if name == "list_pending_reviews":
        rows = manager_tools.list_pending_reviews(p, db)
        return {
            "data": [{"id": str(r.id), "item_count": r.item_count} for r in rows.data],
            "count": rows.count,
        }
    assert isinstance(body, (RequestInput, PrepareInput))
    request_id = body.request_id
    try:
        detail = manager_tools.get_review(p, db, request_id)
    except EmployeeRecordNotFound, service.ReviewForbidden:
        return None
    projected = s.Review.model_validate(detail)
    if isinstance(body, PrepareInput):
        if body.revision != detail.revision:
            raise RequestConflict("内容已变化，请重新查看")
        service.validate_decisions(detail, body.decisions)
        card = s.ReviewCard(**projected.model_dump(), decisions=body.decisions)
        card.actions = [
            s.ReviewAction(
                action=a,
                request_id=detail.id,
                revision=detail.revision,
                card_id=card.card_id,
            )
            for a in ("confirm_review", "continue_review")
        ]
        cards.append(card)
    return projected.model_dump(mode="json")
