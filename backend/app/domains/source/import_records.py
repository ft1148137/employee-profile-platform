"""The manual console and assistant use the same work-record import."""

from hashlib import sha256

from fastapi import HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from app.authz.principal import Principal
from app.domains.source.schemas import SyncRunPublic
from app.domains.source.sync import create_run
from app.domains.work.records import (
    ParsedWorkRecords,
    WorkRecordIssue,
    parse_work_records,
)

MAX_FILE_SIZE = 5 * 1024 * 1024


class CsvImportPublic(BaseModel):
    run: SyncRunPublic
    issues: list[WorkRecordIssue]


class InvalidWorkRecords(HTTPException):
    def __init__(self, issues: list[WorkRecordIssue]) -> None:
        self.issues = issues
        super().__init__(
            422,
            {
                "code": "work_records_invalid",
                "issues": [i.model_dump() for i in issues],
            },
        )


class WorkRecordsTooLarge(HTTPException):
    def __init__(self) -> None:
        super().__init__(413, "文件超过 5 MB，请拆分后再上传")


def preview_work_records(content: bytes, filename: str) -> ParsedWorkRecords:
    if len(content) > MAX_FILE_SIZE:
        raise WorkRecordsTooLarge()
    return parse_work_records(content, filename)


def import_work_records(
    session: Session,
    principal: Principal,
    content: bytes,
    filename: str,
    *,
    commit: bool = True,
) -> CsvImportPublic:
    parsed = preview_work_records(content, filename)
    if parsed.has_errors:
        raise InvalidWorkRecords(parsed.issues)
    run = create_run(
        session,
        principal,
        "csv",
        {
            "filename": filename,
            "file_sha256": sha256(content).hexdigest(),
            "rows": [r.model_dump(mode="json") for r in parsed.rows],
        },
        commit=commit,
    )
    return CsvImportPublic(run=run, issues=parsed.issues)
