import csv
from datetime import date, datetime, timedelta
from io import BytesIO, StringIO
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from app.domains.work import records
from app.domains.work.records import COLUMNS, EXAMPLE_ROW, parse_work_records


def csv_bytes(rows: list[list[object]], encoding: str = "utf-8") -> bytes:
    output = StringIO(newline="")
    csv.writer(output).writerows(rows)
    return output.getvalue().encode(encoding)


def xlsx_bytes(rows: list[list[object]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    for row in rows:
        sheet.append(row)
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "gb18030"])
def test_csv_encodings_reordered_aliases_and_cleaning(encoding: str) -> None:
    headers = [f" {name.upper()} " for name in reversed(COLUMNS)] + ["多余列"]
    result = parse_work_records(
        csv_bytes([[], headers, list(reversed(EXAMPLE_ROW)) + ["忽略"]], encoding),
        "记录.CSV",
    )
    assert not result.has_errors
    assert result.column_mapping["RECORD_ID"] == "record_id"
    assert result.rows[0].row_number == 3
    assert result.rows[0].actor.model_dump() == {
        "account": "example.lin",
        "name": "示例林同学",
        "role": "主持人",
    }
    assert result.rows[0].participants[1].role == ""
    assert [(i.row_number, i.column, i.level) for i in result.issues] == [
        (2, "多余列", "warning")
    ]
    assert result.model_dump()["has_errors"] is False


@pytest.mark.parametrize(
    "value",
    [
        "2026-01-02T03:04:00+03:00",
        "2026-01-02T03:04:00Z",
        "2026-01-02 03:04",
        "2026/01/02 03:04",
        "2026-01-02",
        datetime(2026, 1, 2, 3, 4),
        date(2026, 1, 2),
    ],
)
def test_chinese_xlsx_dates(value: object) -> None:
    row: list[object] = list(EXAMPLE_ROW)
    row[3] = value
    result = parse_work_records(xlsx_bytes([list(COLUMNS.values()), row]), "记录.xlsx")
    assert not result.has_errors
    parsed = result.rows[0].occurred_at
    assert parsed.date() == date(2026, 1, 2)
    naive = not isinstance(value, str) or ("+" not in value and not value.endswith("Z"))
    assert len(result.issues) == int(naive)
    if naive:
        assert parsed.utcoffset() == timedelta(hours=8)
        assert (
            result.issues[0].row_number,
            result.issues[0].column,
            result.issues[0].level,
        ) == (2, "occurred_at", "warning")


@pytest.mark.parametrize(
    "content, filename, message",
    [
        (b"", "empty.csv", "没有工作记录"),
        (b"\n,\n", "empty.csv", "没有工作记录"),
        (csv_bytes([list(COLUMNS)]), "empty.csv", "没有工作记录"),
        (b"bad", "bad.xls", "暂不支持"),
        (b"bad", "bad.xlsx", "文件无法读取"),
        (b"\xff", "bad.csv", "文件无法读取"),
        (b'"unclosed', "bad.csv", "文件无法读取"),
    ],
)
def test_file_errors(content: bytes, filename: str, message: str) -> None:
    result = parse_work_records(content, filename)
    assert result.has_errors
    assert not result.rows
    assert result.issues[0].row_number is None
    assert message in result.issues[0].message


@pytest.mark.parametrize("column", records.REQUIRED)
def test_missing_required_column(column: str) -> None:
    result = parse_work_records(
        csv_bytes([[name for name in COLUMNS if name != column]]), "missing.csv"
    )
    assert result.has_errors
    assert [(i.row_number, i.column, i.level) for i in result.issues] == [
        (None, column, "error")
    ]


@pytest.mark.parametrize(
    "column, value",
    [
        *((name, "") for name in records.REQUIRED),
        ("occurred_at", "2026-02-30"),
        ("occurred_at", "不是时间"),
        ("title", "字" * 501),
        ("detail", "字" * 4001),
        ("participants", ":角色"),
        ("participants", "账号:角色:多余"),
    ],
)
def test_row_errors(column: str, value: str) -> None:
    row = dict(zip(COLUMNS, EXAMPLE_ROW, strict=True))
    row[column] = value
    result = parse_work_records(
        csv_bytes([list(COLUMNS), list(row.values())]), "记录.csv"
    )
    assert result.has_errors
    assert not result.rows
    assert [(i.row_number, i.column, i.level) for i in result.issues] == [
        (2, column, "error")
    ]


def test_duplicate_headers_and_short_rows() -> None:
    duplicate = parse_work_records(
        csv_bytes([list(COLUMNS) + ["记录编号"]]), "记录.csv"
    )
    assert duplicate.has_errors
    assert (duplicate.issues[0].row_number, duplicate.issues[0].column) == (
        1,
        "record_id",
    )
    short = parse_work_records(csv_bytes([list(COLUMNS), ["1"]]), "记录.csv")
    assert short.has_errors
    assert {i.column for i in short.issues} == set(records.REQUIRED) - {"record_id"}


def test_duplicate_record_ids_are_scoped_to_source_and_rows_continue() -> None:
    row = list(EXAMPLE_ROW)
    other = row.copy()
    other[1] = "其他系统"
    result = parse_work_records(
        csv_bytes([list(COLUMNS), row, [], other, row]), "记录.csv"
    )
    assert result.has_errors
    assert len(result.rows) == 2
    assert [(i.row_number, i.column) for i in result.issues] == [(5, "record_id")]
    assert "第 2 行" in result.issues[0].message


def test_participants_separators_optional_roles_and_self_warning() -> None:
    row = list(EXAMPLE_ROW)
    row[7] = " example.chen: 评审人； example.wang ;example.lin:主持人; "
    result = parse_work_records(csv_bytes([list(COLUMNS), row]), "记录.csv")
    assert not result.has_errors
    assert [(p.account, p.role) for p in result.rows[0].participants] == [
        ("example.chen", "评审人"),
        ("example.wang", ""),
    ]
    assert [(i.row_number, i.column, i.level) for i in result.issues] == [
        (2, "participants", "warning")
    ]


def test_optional_columns_and_length_boundaries() -> None:
    fields = dict(zip(COLUMNS, EXAMPLE_ROW, strict=True))
    fields["title"] = "字" * 500
    fields["detail"] = "字" * 4000
    result = parse_work_records(
        csv_bytes([list(fields), list(fields.values())]), "记录.csv"
    )
    assert not result.issues
    minimal = parse_work_records(
        csv_bytes(
            [list(records.REQUIRED), [fields[name] for name in records.REQUIRED]]
        ),
        "记录.csv",
    )
    assert not minimal.issues
    assert minimal.rows[0].participants == []
    assert minimal.rows[0].detail == minimal.rows[0].actor.name == ""


@pytest.mark.parametrize("size", [20000, 20001])
def test_row_limit_counts_invalid_and_valid_records(size: int) -> None:
    rows: list[list[object]] = [list(COLUMNS), []]
    rows.extend([[str(index), *EXAMPLE_ROW[1:]] for index in range(size)])
    result = parse_work_records(csv_bytes(rows), "记录.csv")
    assert len(result.rows) == 20000
    assert result.has_errors == (size > 20000)
    if result.has_errors:
        assert result.issues[-1].row_number == 20003
    invalid = parse_work_records(
        csv_bytes([list(COLUMNS)] + [[str(index)] for index in range(size)]), "记录.csv"
    )
    assert ("超过 20000 行" in invalid.issues[-1].message) == (size > 20000)


def test_uncached_formula_in_known_column_is_error() -> None:
    row = list(EXAMPLE_ROW)
    row[9] = "=1+1"
    result = parse_work_records(xlsx_bytes([list(COLUMNS), row]), "记录.xlsx")
    assert result.has_errors
    assert (result.issues[0].row_number, result.issues[0].column) == (2, "title")
    assert "公式没有计算结果" in result.issues[0].message


@pytest.mark.parametrize("xlsx", [False, True])
def test_templates_round_trip(xlsx: bool) -> None:
    content = (
        records.build_work_records_template_xlsx()
        if xlsx
        else records.build_work_records_template_csv()
    )
    result = parse_work_records(content, "模板.xlsx" if xlsx else "模板.csv")
    assert not result.issues
    assert len(result.rows) == 1
    assert tuple(result.column_mapping) == tuple(COLUMNS.values())
    if not xlsx:
        assert content.startswith(b"\xef\xbb\xbf")


def test_xlsx_template_all_cells_are_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(records, "EXAMPLE_ROW", tuple("=1+1" for _ in COLUMNS))
    workbook = load_workbook(
        BytesIO(records.build_work_records_template_xlsx()), data_only=False
    )
    assert all(
        cell.data_type == "s" and cell.number_format == "@"
        for row in workbook.worksheets[0]
        for cell in row
    )
    assert workbook.worksheets[0]["A2"].value == "=1+1"
    workbook.close()


def test_all_ten_synthetic_enterprises() -> None:
    paths = sorted(
        (Path(__file__).resolve().parents[3] / "examples/test-enterprises").glob(
            "*/work_records.csv"
        )
    )
    assert len(paths) == 10
    total = 0
    for path in paths:
        result = parse_work_records(path.read_bytes(), path.name)
        assert not result.has_errors, (path, result.issues)
        total += len(result.rows)
        if path.parent.name == "qiguang-aiot":
            plm = next(row for row in result.rows if row.record_id == "PLM-0001")
            assert [(p.account, p.role) for p in plm.participants] == [
                ("qiao.yanzhi", "主讲人"),
                ("su.tang", "评审人"),
                ("chu.mingye", "评审人"),
            ]
    assert total == 596
