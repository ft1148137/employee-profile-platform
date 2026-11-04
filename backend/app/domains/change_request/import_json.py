import json
from pathlib import Path
from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import EmailStr, Field, ValidationError
from sqlmodel import Session

from app.authz.principal import Principal
from app.domains.change_request import employee
from app.domains.change_request.models import ChangeOp
from app.domains.change_request.schemas import ItemInput, RequestPreview, ToolInput
from app.domains.org.models import EmploymentStatus
from app.domains.org.service import visible_employee

FILES_DIR = Path(__file__).resolve().parents[4] / "templates/profile-import"
MAX_FILE_SIZE = 1024 * 1024
Text = Annotated[str, Field(min_length=1, max_length=4000)]
Key = Annotated[str, Field(min_length=1, max_length=100)]


class ImportItem(ToolInput):
    capability_key: Key
    statement: Text
    basis: Text
    level: Key | None = None
    uncertainty: Text | None = None


class ProfileImport(ToolInput):
    schema_version: Literal["1"]
    employee_email: Annotated[EmailStr, Field(max_length=254)]
    capability_schema_version: Annotated[int, Field(strict=True, ge=1)]
    items: Annotated[list[ImportItem], Field(min_length=1, max_length=200)]


class InvalidImport(HTTPException):
    def __init__(self, messages: list[str], status_code: int = 422) -> None:
        super().__init__(
            status_code,
            {"message": "画像导入失败，请修改文件后重试", "blockers": messages},
        )


def parse_import(content: bytes) -> ProfileImport:
    if len(content) > MAX_FILE_SIZE:
        raise InvalidImport(["文件：大小不能超过 1 MB"], 413)
    try:
        data = json.loads(content.decode("utf-8-sig"))
    except UnicodeError:
        raise InvalidImport(["文件：请使用 UTF-8 编码"]) from None
    except json.JSONDecodeError as error:
        raise InvalidImport([f"文件：第 {error.lineno} 行 JSON 格式错误"]) from None
    except ValueError:
        raise InvalidImport(["文件：JSON 数字超出支持范围"]) from None
    except RecursionError:
        raise InvalidImport(["文件：JSON 嵌套过深"]) from None
    if isinstance(data, list):
        raise InvalidImport(["文件：员工导入只能包含本人的一份画像"])
    try:
        return ProfileImport.model_validate(data)
    except ValidationError as error:
        messages = []
        translations = {
            "missing": "缺少必填字段",
            "extra_forbidden": "不允许的多余字段",
            "string_too_long": "文字超过长度上限",
            "string_too_short": "文字不能为空",
            "literal_error": "只支持格式版本 1",
            "greater_than_equal": "必须为正整数",
            "too_short": "至少需要一项",
            "too_long": "超过数量或长度上限",
            "value_error": "字段值无效，请检查邮箱格式",
        }
        for item in error.errors():
            loc = item["loc"]
            label = (
                f"第 {int(loc[1]) + 1} 项 " + ".".join(map(str, loc[2:]))
                if len(loc) > 1 and loc[0] == "items" and isinstance(loc[1], int)
                else ".".join(map(str, loc)) or "文件"
            )
            messages.append(
                f"{label}：{translations.get(item['type'], '字段类型或取值不正确')}"
            )
        raise InvalidImport(messages) from None


def import_profile(
    principal: Principal,
    session: Session,
    login_email: str | None,
    content: bytes,
    filename: str,
    *,
    confirm: bool,
) -> RequestPreview:
    data = parse_import(content)
    subject = visible_employee(session, principal, employee.employee_id(principal))
    if subject.employment_status != EmploymentStatus.ACTIVE:
        raise InvalidImport(["employee_email：仅在职员工可以导入画像"])
    if not login_email or data.employee_email.casefold() != login_email.casefold():
        raise InvalidImport(["employee_email：文件里的邮箱不是你的登录邮箱"])
    current = {
        i.capability.key
        for i in employee.my_profile(principal, session).items
        if i.claim
    }
    items = [
        ItemInput(
            op=ChangeOp.MODIFY if i.capability_key in current else ChangeOp.ADD,
            capability_key=i.capability_key,
            statement=i.statement,
            level=i.level,
            owner_statement=i.basis,
            uncertainty=i.uncertainty,
        )
        for i in data.items
    ]
    client_name = filename.replace("\\", "/").rsplit("/", 1)[-1][:200] or "profile.json"
    try:
        draft = employee.save_draft(
            principal, session, None, items, "import", client_name
        )
    except employee.InvalidItems as error:
        fields = {
            "能力 key": "capability_key",
            "等级 key": "level",
            "同一能力": "capability_key",
        }
        raise InvalidImport(
            [
                f"第 {int(e['index']) + 1} 项 "
                f"{next((field for prefix, field in fields.items() if str(e['message']).startswith(prefix)), 'items')}：{e['message']}"
                for e in error.errors
            ]
        ) from None
    result = employee.preview(principal, session, draft.id)
    if confirm:
        employee.submit(
            principal, session, draft.id, result.revision, "import", client_name
        )
        session.commit()
    else:
        session.rollback()
    return result
