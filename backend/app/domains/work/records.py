import csv
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO
from pathlib import Path
from typing import Literal
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

from openpyxl import Workbook
from openpyxl.utils.exceptions import InvalidFileException
from pydantic import AwareDatetime, BaseModel, Field, computed_field

from app.domains.org.roster.parser import FileRow, read_csv_rows, read_xlsx_rows

COLUMNS = {
    "record_id": "记录编号",
    "source": "来源",
    "record_type": "记录类型",
    "occurred_at": "发生时间",
    "actor_account": "执行人账号",
    "actor_name": "执行人姓名",
    "actor_role": "执行人角色",
    "participants": "参与人",
    "project": "项目",
    "title": "标题",
    "detail": "说明",
    "status": "状态",
}
REQUIRED = ("record_id", "source", "occurred_at", "actor_account", "title")
ALIASES = {alias: name for name, label in COLUMNS.items() for alias in (name, label)}
EXAMPLE_ROW = (
    "EXAMPLE-001",
    "示例系统",
    "设计评审",
    "2026-01-01T09:00:00+08:00",
    "example.lin",
    "示例林同学",
    "主持人",
    "example.chen:评审人;example.wang",
    "示例项目",
    "示例：方案评审",
    "虚构示例，请删除后填写企业工作记录。",
    "已完成",
)
MAX_ROWS = 20000


class RecordPerson(BaseModel):
    account: str
    name: str = ""
    role: str = ""


class WorkRecordRow(BaseModel):
    row_number: int
    record_id: str
    source: str
    record_type: str
    occurred_at: AwareDatetime
    actor: RecordPerson
    participants: list[RecordPerson]
    project: str
    title: str
    detail: str
    status: str


class WorkRecordIssue(BaseModel):
    row_number: int | None
    column: str | None
    level: Literal["error", "warning"]
    message: str


class ParsedWorkRecords(BaseModel):
    rows: list[WorkRecordRow] = Field(default_factory=list)
    issues: list[WorkRecordIssue] = Field(default_factory=list)
    column_mapping: dict[str, str] = Field(default_factory=dict)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_errors(self) -> bool:
        return any(issue.level == "error" for issue in self.issues)


def _issue(
    result: ParsedWorkRecords,
    message: str,
    row: int | None = None,
    column: str | None = None,
    level: Literal["error", "warning"] = "error",
) -> None:
    result.issues.append(
        WorkRecordIssue(
            row_number=row,
            column=column,
            level=level,
            message=message,
        )
    )


def _occurred_at(value: str, result: ParsedWorkRecords, number: int) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("/", "-"))
    except ValueError:
        _issue(
            result,
            "时间无法解析，请填写 ISO 8601 或 年-月-日 时:分",
            number,
            "occurred_at",
        )
        return None
    if parsed.tzinfo is None:
        # shortcut: Assume Beijing time until organization timezone settings are available.
        parsed = parsed.replace(tzinfo=timezone(timedelta(hours=8)))
        _issue(
            result,
            "时间没有时区，已按北京时间（+08:00）处理",
            number,
            "occurred_at",
            "warning",
        )
    return parsed


def _participants(
    value: str, actor: str, result: ParsedWorkRecords, number: int
) -> list[RecordPerson]:
    people = []
    for item in value.replace("；", ";").split(";"):
        if not item.strip():
            continue
        account, _, role = item.partition(":")
        account, role = account.strip(), role.strip()
        if not account or ":" in role:
            _issue(
                result,
                "参与人格式不正确，请填写 账号:角色，多个用分号分隔",
                number,
                "participants",
            )
        elif account == actor:
            _issue(
                result,
                "参与人中包含执行人本人，已忽略这一项",
                number,
                "participants",
                "warning",
            )
        else:
            people.append(RecordPerson(account=account, role=role))
    return people


def _parse_rows(result: ParsedWorkRecords, file_rows: Iterator[FileRow]) -> None:
    columns: dict[str, int] | None = None
    seen: dict[tuple[str, str], int] = {}
    count = 0
    for number, raw, formulas in file_rows:
        values = [str(value).strip() if value is not None else "" for value in raw]
        if not any(values) and not formulas:
            continue
        if columns is None:
            columns = {}
            for index, header in enumerate(values):
                standard = ALIASES.get(header.casefold())
                if standard:
                    result.column_mapping[header] = standard
                    if standard in columns:
                        _issue(
                            result, "有多个列对应这一项，请只保留一列", number, standard
                        )
                    columns[standard] = index
                elif header:
                    _issue(
                        result, f"已忽略不认识的列：{header}", number, header, "warning"
                    )
            for name in REQUIRED:
                if name not in columns:
                    _issue(result, f"缺少必填列：{COLUMNS[name]}", column=name)
            if result.has_errors:
                return
            continue
        count += 1
        if count > MAX_ROWS:
            _issue(result, "工作记录超过 20000 行，请拆分文件后导入", number)
            return
        fields = {
            name: values[index] if index < len(values) else ""
            for name, index in columns.items()
        }
        for name in COLUMNS:
            fields.setdefault(name, "")
        issue_start = len(result.issues)
        for name, index in columns.items():
            if index in formulas:
                _issue(
                    result,
                    "公式没有计算结果，请在 Excel 中计算并保存，或粘贴为值",
                    number,
                    name,
                )
        for name in REQUIRED:
            if not fields[name]:
                _issue(result, "不能为空", number, name)
        for name, limit in (("title", 500), ("detail", 4000)):
            if len(fields[name]) > limit:
                _issue(result, f"不能超过 {limit} 字", number, name)
        occurred = (
            _occurred_at(fields["occurred_at"], result, number)
            if fields["occurred_at"]
            else None
        )
        participants = _participants(
            fields["participants"], fields["actor_account"], result, number
        )
        key = (fields["source"], fields["record_id"])
        if all(key):
            if key in seen:
                _issue(
                    result,
                    f"来源和记录编号重复，与第 {seen[key]} 行相同",
                    number,
                    "record_id",
                )
            seen.setdefault(key, number)
        if occurred is None or any(
            issue.level == "error" for issue in result.issues[issue_start:]
        ):
            continue
        result.rows.append(
            WorkRecordRow(
                row_number=number,
                occurred_at=occurred,
                actor=RecordPerson(
                    account=fields["actor_account"],
                    name=fields["actor_name"],
                    role=fields["actor_role"],
                ),
                participants=participants,
                record_id=fields["record_id"],
                source=fields["source"],
                record_type=fields["record_type"],
                project=fields["project"],
                title=fields["title"],
                detail=fields["detail"],
                status=fields["status"],
            )
        )
    if count == 0:
        _issue(result, "文件中没有工作记录，请下载模板填写")


def parse_work_records(content: bytes, filename: str) -> ParsedWorkRecords:
    result = ParsedWorkRecords()
    extension = Path(filename).suffix.lower()
    if extension not in (".csv", ".xlsx"):
        _issue(result, "暂不支持这种文件格式，请另存为 .xlsx 或 .csv")
        return result
    file_rows = (
        read_csv_rows(content) if extension == ".csv" else read_xlsx_rows(content)
    )
    try:
        _parse_rows(result, file_rows)
    except (
        UnicodeError,
        csv.Error,
        BadZipFile,
        InvalidFileException,
        ParseError,
        KeyError,
        IndexError,
        ValueError,
        OSError,
    ):
        _issue(
            result, "文件无法读取，请检查文件是否损坏，并另存为 .xlsx 或 .csv 后重试"
        )
    finally:
        file_rows.close()
    return result


def build_work_records_template_xlsx() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "工作记录"
    for number, values in enumerate((tuple(COLUMNS.values()), EXAMPLE_ROW), 1):
        for column, value in enumerate(values, 1):
            cell = sheet.cell(number, column, value)
            cell.data_type = "s"
            cell.number_format = "@"
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def build_work_records_template_csv() -> bytes:
    output = StringIO(newline="")
    csv.writer(output).writerows((tuple(COLUMNS.values()), EXAMPLE_ROW))
    return output.getvalue().encode("utf-8-sig")
