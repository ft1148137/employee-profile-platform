import csv
import re
import unicodedata
from collections.abc import Generator, Iterator
from io import BytesIO, StringIO
from pathlib import Path
from typing import Literal
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, ZipFile

from email_validator import EmailNotValidError, validate_email
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from app.domains.org.roster.schemas import (
    HEADERS,
    ParsedRoster,
    RosterIssue,
    RosterRow,
)

ALIASES = {
    "工号": ("员工编号", "员工工号", "编号"),
    "姓名": ("员工姓名", "名字"),
    "部门": ("所属部门", "部门路径"),
    "职位": ("岗位", "职务"),
    "上级工号": ("直属上级工号", "汇报对象工号", "上级"),
    "邮箱": ("工作邮箱", "电子邮箱", "邮件"),
}
STANDARD_COLUMNS = {
    alias: name for name, aliases in ALIASES.items() for alias in (name, *aliases)
}
MAX_ROWS = 5000
FileRow = tuple[int, list[object], set[int]]


def _clean(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return unicodedata.normalize("NFC", str(value)).strip()


def _issue(
    result: ParsedRoster,
    message: str,
    row: int | None = None,
    column: str | None = None,
    severity: Literal["error", "warning"] = "error",
) -> None:
    location = f"第 {row} 行" if row is not None else ""
    if column:
        location += f"「{column}」"
    result.issues.append(
        RosterIssue(
            severity=severity,
            row_number=row,
            column=column,
            message=f"{location}：{message}" if location else message,
        )
    )


def read_csv_rows(content: bytes) -> Generator[FileRow]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("gb18030")
    for number, values in enumerate(csv.reader(StringIO(text), strict=True), 1):
        yield number, list(values), set()


def read_xlsx_rows(content: bytes) -> Generator[FileRow]:
    workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
    try:
        # Avoid decoding every cell twice in the usual case without formulas.
        with ZipFile(BytesIO(content)) as archive:
            has_formulas = any(
                re.search(rb"<(?:[\w.-]+:)?f[\s>/]", archive.read(name))
                for name in archive.namelist()
                if name.startswith("xl/worksheets/") and name.endswith(".xml")
            )
        if not has_formulas:
            for number, values in enumerate(
                workbook.worksheets[0].iter_rows(values_only=True), 1
            ):
                yield number, list(values), set()
            return
        formulas = load_workbook(BytesIO(content), read_only=True, data_only=False)
        try:
            sheet = workbook.worksheets[0]
            formula_sheet = formulas.worksheets[0]
            for number, (cells, originals) in enumerate(
                zip(sheet.iter_rows(), formula_sheet.iter_rows(), strict=True), 1
            ):
                missing = {
                    index
                    for index, cell in enumerate(originals)
                    if cell.data_type == "f" and cells[index].value is None
                }
                yield number, [cell.value for cell in cells], missing
        finally:
            formulas.close()
    finally:
        workbook.close()


def _headers(result: ParsedRoster, values: list[str], number: int) -> dict[str, int]:
    columns: dict[str, int] = {}
    ignored = []
    for index, header in enumerate(values):
        standard = STANDARD_COLUMNS.get(header)
        if standard:
            result.column_mapping[header] = standard
            if standard in columns:
                _issue(result, "有多个列对应这一项，请只保留一列", number, standard)
            columns[standard] = index
        elif header:
            ignored.append(header)
    missing = [name for name in (*HEADERS[:3], "邮箱") if name not in columns]
    if missing:
        _issue(result, f"缺少必需列：{'、'.join(missing)}，请下载名单模板填写", number)
    if ignored:
        _issue(
            result,
            f"已忽略不认识的列：{'、'.join(ignored)}",
            number,
            severity="warning",
        )
    return columns


def _parse_rows(result: ParsedRoster, file_rows: Iterator[FileRow]) -> None:
    columns: dict[str, int] | None = None
    for number, raw, missing_formulas in file_rows:
        values = [_clean(value) for value in raw]
        if not any(values) and not missing_formulas:
            continue
        for index in sorted(missing_formulas):
            column = next(
                (
                    name
                    for name, position in (columns or {}).items()
                    if position == index
                ),
                f"第 {index + 1} 列",
            )
            _issue(
                result,
                "公式没有计算结果，请在 Excel 中计算并保存，或粘贴为值",
                number,
                column,
            )
        if columns is None:
            columns = _headers(result, values, number)
            if result.has_errors:
                return
            continue
        if len(result.rows) == MAX_ROWS:
            _issue(result, "名单超过 5000 行，请将完整名单控制在 5000 行以内", number)
            return
        fields = {
            name: values[index] if index < len(values) else ""
            for name, index in columns.items()
        }
        department = tuple(
            part.strip()
            for part in fields["部门"].replace("／", "/").split("/")
            if part.strip()
        )
        for name in HEADERS[:3]:
            if not fields[name] or (name == "部门" and not department):
                _issue(result, "不能为空", number, name)
        for name in ("工号", "上级工号"):
            position = columns.get(name)
            if (
                position is not None
                and position < len(raw)
                and type(raw[position]) in (int, float)
            ):
                _issue(
                    result,
                    "工号是数字格式，开头的 0 可能已经丢失，建议把工号列设为文本",
                    number,
                    name,
                    "warning",
                )
        result.rows.append(
            RosterRow(
                row_number=number,
                employee_code=fields["工号"],
                display_name=fields["姓名"],
                department_path=department,
                title=fields.get("职位") or None,
                manager_code=fields.get("上级工号") or None,
                email=fields.get("邮箱", "").lower() or None,
            )
        )
    if columns is None:
        _issue(result, "文件中没有名单，请下载名单模板填写")


def validate_roster(result: ParsedRoster) -> None:
    employees: dict[str, RosterRow] = {}
    emails: dict[str, int] = {}
    for row in result.rows:
        code = row.employee_code.casefold()
        if code:
            if code in employees:
                _issue(
                    result,
                    f"工号重复，与第 {employees[code].row_number} 行相同",
                    row.row_number,
                    "工号",
                )
            employees.setdefault(code, row)
        if not row.email:
            _issue(result, "不能为空，请补上邮箱后重新上传", row.row_number, "邮箱")
        else:
            try:
                validate_email(row.email, check_deliverability=False)
            except EmailNotValidError:
                _issue(
                    result,
                    "邮箱格式不正确，请补上完整的邮箱地址",
                    row.row_number,
                    "邮箱",
                )
            email = row.email.casefold()
            if email in emails:
                _issue(
                    result,
                    f"邮箱重复，与第 {emails[email]} 行相同",
                    row.row_number,
                    "邮箱",
                )
            emails.setdefault(email, row.row_number)
    for row in result.rows:
        if row.manager_code:
            manager = row.manager_code.casefold()
            if manager == row.employee_code.casefold():
                _issue(result, "上级不能是自己", row.row_number, "上级工号")
            elif manager not in employees:
                _issue(
                    result,
                    f"名单里没有工号 {row.manager_code}",
                    row.row_number,
                    "上级工号",
                )
    # Each employee has at most one manager; iterative traversal stays O(n).
    visited: set[str] = set()
    for code in employees:
        path: list[str] = []
        positions: dict[str, int] = {}
        current = code
        while (
            current in employees and current not in visited and current not in positions
        ):
            positions[current] = len(path)
            path.append(current)
            current = (employees[current].manager_code or "").casefold()
        if current in positions:
            cycle = path[positions[current] :]
            if len(cycle) > 1:
                codes = [employees[item].employee_code for item in [*cycle, current]]
                _issue(
                    result,
                    f"汇报关系成环：{' → '.join(codes)}",
                    employees[current].row_number,
                    "上级工号",
                )
        visited.update(path)


def parse_roster(content: bytes, filename: str) -> ParsedRoster:
    result = ParsedRoster()
    extension = Path(filename).suffix.lower()
    if extension not in (".csv", ".xlsx"):
        _issue(result, "暂不支持这种文件格式，请在 Excel 里另存为 .xlsx 或 .csv")
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
            result,
            "文件无法读取，请检查文件是否损坏，并在 Excel 里另存为 .xlsx 或 .csv 后重试",
        )
    finally:
        file_rows.close()
    validate_roster(result)
    return result
