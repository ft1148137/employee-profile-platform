import asyncio
from uuid import UUID

from fastapi import HTTPException
from pydantic import JsonValue, ValidationError
from sqlmodel import Session

from app.authz.principal import Principal
from app.domains.admin.overview import build_overview
from app.domains.admin_assistant import change_set, registry, store
from app.domains.admin_assistant.schemas import (
    ChangeSetCard,
    FileUploadCard,
    LinkCard,
    ProposeChanges,
    RequestFile,
    SecretInputCard,
)
from app.domains.bot import attachments
from app.domains.change_request.import_json import InvalidImport
from app.domains.change_request.schemas import ToolInput
from app.domains.drafting.settings import AutoDraftDisabled
from app.domains.model.pi_client import ToolDefinition
from app.domains.org.roster.service import RosterError


class ListFunctionsInput(ToolInput):
    area: registry.Area


class ReadInput(ToolInput):
    function: str
    args: dict[str, JsonValue]


class RequestSecretInput(ToolInput):
    function: str
    field: str


def title(name: str, args: dict[str, JsonValue]) -> str:
    if name in ("admin_read", "propose_changes"):
        function = args.get("function")
        if name == "propose_changes":
            items = args.get("items")
            function = (
                items[0].get("function")
                if isinstance(items, list) and items and isinstance(items[0], dict)
                else None
            )
        entry = registry.FUNCTIONS.get(function) if isinstance(function, str) else None
        return (
            entry.description
            if entry
            else "读取信息"
            if name == "admin_read"
            else "起草变更"
        )
    return {
        "read_attachment": "读取会话附件",
        "get_today": "查看今天要处理的事",
        "list_admin_functions": "查看管理功能",
        "request_file": "请求上传文件",
        "request_secret_input": "请求安全输入",
    }.get(name, "管理功能")


def definitions() -> list[ToolDefinition]:
    return [
        attachments.READ_TOOL,
        ToolDefinition(
            name="request_file",
            description="显示文件上传框；只接收文件种类，文件内容由管理员直接上传",
            parameters=RequestFile.model_json_schema(),
        ),
        ToolDefinition(
            name="request_secret_input",
            description="为已起草改动的密钥字段显示安全输入框，不能接收密钥值",
            parameters=RequestSecretInput.model_json_schema(),
        ),
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
        ToolDefinition(
            name="propose_changes",
            description="起草改动并显示确认卡，只有管理员点卡片才会生效",
            parameters=ProposeChanges.model_json_schema(),
        ),
    ]


def call(
    db: Session,
    principal: Principal,
    cards: list[
        LinkCard | registry.TableCard | ChangeSetCard | SecretInputCard | FileUploadCard
    ],
    name: str,
    args: dict[str, JsonValue],
    *,
    session_id: UUID | None = None,
) -> JsonValue:
    if name == "read_attachment" and session_id:
        try:
            body_attachment = attachments.ReadAttachmentInput.model_validate(args)
            return attachments.read_attachment(
                db, principal, "admin", session_id, body_attachment.attachment_id
            )
        except ValidationError:
            return {"error": "工具参数不正确"}
        except HTTPException:
            return {"error": "附件不存在"}
    try:
        if name == "request_file" and session_id:
            store.get_session(db, principal, session_id)
            cards.append(
                FileUploadCard(**RequestFile.model_validate(args).model_dump())
            )
            return {"data": {"message": "请在上传框选择文件，预览后点击确认才会导入"}}
        if name == "request_secret_input" and session_id:
            body_secret = RequestSecretInput.model_validate(args)
            cards.append(
                change_set.request_secret_input(
                    db, principal, session_id, body_secret.function, body_secret.field
                )
            )
            return {"data": {"message": "请在安全输入框填写"}}
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
    except HTTPException:
        return {"data": {"error": "请先起草改动，并选择允许安全输入的字段"}}
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
                        item.model_dump(
                            mode="json",
                            exclude={"args", "impact", "before", "after"}
                            if item.function == "issue_accounts"
                            else {"args"},
                        )
                        for item in card.change_set.items
                    ],
                }
            }
        except ValidationError:
            return {"error": "参数不正确，请按功能说明填写"}
        except InvalidImport as error:
            return {"error": "；".join(error.messages)}
        except HTTPException as error:
            return {
                "error": str(error.detail)
                if isinstance(
                    error, (change_set.ChangeSetError, AutoDraftDisabled, RosterError)
                )
                else "找不到这份改动或会话"
            }
    if name != "get_today" or args:
        return {"error": "工具或参数不正确，请使用已开放的功能"}
    overview = build_overview(db, principal)
    if not cards:
        cards.append(
            LinkCard(title="查看数据来源和同步", page="/admin/data-ai?tab=sources")
        )
    return {"data": overview.model_dump(mode="json")}
