from uuid import UUID

from fastapi import HTTPException
from pydantic import JsonValue, ValidationError
from sqlmodel import Session

from app.authz.principal import Principal
from app.domains.admin.overview import build_overview
from app.domains.admin_assistant import change_set
from app.domains.admin_assistant.schemas import ChangeSetCard, LinkCard, ProposeChanges
from app.domains.change_request.schemas import ToolInput
from app.domains.model.pi_client import ToolDefinition


def definitions() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="get_today",
            description="查看今天要处理的事和运行状态",
            parameters=ToolInput.model_json_schema(),
        ),
        ToolDefinition(
            name="propose_changes",
            description="起草改动并显示确认卡，只有管理员点卡片才会生效",
            parameters=ProposeChanges.model_json_schema(),
        ),
    ]


def call(
    db: Session,
    principal: Principal,
    cards: list[LinkCard | ChangeSetCard],
    name: str,
    args: dict[str, JsonValue],
    *,
    session_id: UUID | None = None,
) -> JsonValue:
    if name == "propose_changes" and session_id:
        try:
            row = change_set.propose(
                db, principal, session_id, ProposeChanges.model_validate(args)
            )
            card = ChangeSetCard(change_set=change_set.public(row))
            cards.append(card)
            return {
                "data": {
                    "id": str(row.id),
                    "revision": row.revision,
                    "changes": [
                        item.model_dump(mode="json", exclude={"args"})
                        for item in card.change_set.items
                    ],
                }
            }
        except ValidationError:
            return {"error": "参数不正确，请按功能说明填写"}
        except HTTPException as error:
            return {
                "error": str(error.detail)
                if isinstance(error, change_set.ChangeSetError)
                else "找不到这份改动或会话"
            }
    if name != "get_today" or args:
        return {"error": "工具或参数不正确，目前只能查看今天要处理的事"}
    result = build_overview(db, principal)
    if not cards:
        cards.append(LinkCard(title="打开管理首页", page="/admin"))
    return {"data": result.model_dump(mode="json")}
