from pathlib import Path
from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import EmailStr, Field, RootModel

from app.domains.change_request.schemas import ToolInput

FILES_DIR = Path(__file__).resolve().parents[4] / "templates/profile-import"
Text = Annotated[str, Field(min_length=1, max_length=4000)]
Key = Annotated[str, Field(min_length=1, max_length=100)]


class ImportItem(ToolInput):
    capability_key: Key
    statement: Text
    basis: Text
    level: Key | None = None
    uncertainty: Text | None = None


class ImportPerson(ToolInput):
    schema_version: Literal["1"]
    employee_email: Annotated[EmailStr, Field(max_length=254)]
    capability_schema_version: Annotated[int, Field(strict=True, ge=1)]
    items: Annotated[list[ImportItem], Field(min_length=1, max_length=200)]


class ProfileImport(
    RootModel[
        ImportPerson
        | Annotated[list[ImportPerson], Field(min_length=1, max_length=200)]
    ]
):
    pass


class InvalidImport(HTTPException):
    def __init__(self, messages: list[str], status_code: int = 422) -> None:
        self.messages = messages
        super().__init__(
            status_code,
            {"message": "画像导入失败，请修改文件后重试", "blockers": messages},
        )
