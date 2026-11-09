import asyncio

from pydantic import JsonValue, ValidationError
from sqlmodel import Session

from app.authz.principal import Principal
from app.domains.admin.overview import build_overview
from app.domains.admin_assistant import registry
from app.domains.admin_assistant.schemas import LinkCard
from app.domains.change_request.schemas import ToolInput
from app.domains.model.pi_client import ToolDefinition


class ListFunctionsInput(ToolInput):
    area: registry.Area


class ReadInput(ToolInput):
    function: str
    args: dict[str, JsonValue]


def definitions() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="get_today",
            description="查看今天要处理的事和运行状态",
            parameters=ToolInput.model_json_schema(),
        ),
        ToolDefinition(
            name="list_admin_functions",
            description="查看某个区域的管理功能和参数说明",
            parameters=ListFunctionsInput.model_json_schema(),
        ),
        ToolDefinition(
            name="admin_read",
            description="执行只读管理功能，参数按功能说明填写",
            parameters=ReadInput.model_json_schema(),
        ),
    ]


def call(
    db: Session,
    principal: Principal,
    cards: list[LinkCard | registry.TableCard],
    name: str,
    args: dict[str, JsonValue],
) -> JsonValue:
    try:
        if name == "list_admin_functions":
            return registry.list_admin_functions(
                ListFunctionsInput.model_validate(args).area
            )
        if name == "admin_read":
            body = ReadInput.model_validate(args)
            result = asyncio.run(
                registry.admin_read(db, principal, body.function, body.args)
            )
            if "cards" in result:
                entry = registry.FUNCTIONS[body.function]
                cards.append(LinkCard(title="打开对应页面", page=entry.page))
                cards.extend(
                    registry.TableCard.model_validate(c) for c in result["cards"]
                )
            return {"data": result["data"]}
    except ValidationError:
        return {"data": {"error": "参数不正确，请按功能说明填写"}}
    if name != "get_today" or args:
        return {"data": {"error": "工具或参数不正确，现在只能查看，不能改动"}}
    overview = build_overview(db, principal)
    if not cards:
        cards.append(LinkCard(title="打开管理首页", page="/admin"))
    return {"data": overview.model_dump(mode="json")}
