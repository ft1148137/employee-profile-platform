from pydantic import JsonValue
from sqlmodel import Session

from app.authz.principal import Principal
from app.domains.admin.overview import build_overview
from app.domains.admin_assistant.schemas import LinkCard
from app.domains.change_request.schemas import ToolInput
from app.domains.model.pi_client import ToolDefinition


def definitions() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="get_today",
            description="查看今天要处理的事和运行状态",
            parameters=ToolInput.model_json_schema(),
        )
    ]


def call(
    db: Session,
    principal: Principal,
    cards: list[LinkCard],
    name: str,
    args: dict[str, JsonValue],
) -> JsonValue:
    if name != "get_today" or args:
        return {"error": "工具或参数不正确，目前只能查看今天要处理的事"}
    result = build_overview(db, principal)
    if not cards:
        cards.append(LinkCard(title="打开管理首页", page="/admin"))
    return {"data": result.model_dump(mode="json")}
