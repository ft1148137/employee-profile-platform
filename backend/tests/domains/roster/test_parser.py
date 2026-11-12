import csv
from io import BytesIO, StringIO
from pathlib import Path
from time import perf_counter
from xml.etree import ElementTree
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from openpyxl import Workbook

from app.domains.org.roster.parser import parse_roster
from app.domains.org.roster.schemas import HEADERS


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
    workbook.create_sheet("忽略此表").append(["不是名单"])
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "gb18030"])
def test_csv_encodings_aliases_and_cleaning(encoding: str) -> None:
    content = csv_bytes(
        [
            [],
            [" 员工编号　", "员工姓名", "部门路径", "岗位", "上级", "邮件", "职级"],
            [
                " 001 ",
                "　欧阳䶮㵘𤭢 e\u0301　",
                "／ 公司 / / 研发 ／ 平台 ／",
                "研发",
                "",
                "one@example.com",
            ],
            [],
            ["002", "Alex", "公司/运营/质量组", "", "001", "alex@example.com"],
        ],
        encoding,
    )
    result = parse_roster(content, "名单.CSV")
    assert not result.has_errors
    assert [row.row_number for row in result.rows] == [3, 5]
    assert result.rows[0].employee_code == "001"
    assert result.rows[0].display_name == "欧阳䶮㵘𤭢 é"
    assert result.rows[0].department_path == ("公司", "研发", "平台")
    assert result.rows[1].title is None
    assert result.column_mapping["员工编号"] == "工号"
    assert len(result.issues) == 1
    assert "职级" in result.issues[0].message
    assert result.model_dump()["has_errors"] is False


@pytest.mark.parametrize(
    "content, filename, message",
    [
        (b"", "empty.csv", "没有名单"),
        (b"\n,\n", "empty.csv", "没有名单"),
        (b"bad", "old.xls", "暂不支持这种文件格式"),
        (b"bad", "bad.xlsx", "文件无法读取"),
        (b"\xff", "bad.csv", "文件无法读取"),
        (b'"unclosed', "bad.csv", "文件无法读取"),
        (csv_bytes([["姓名"], ["李宁"]]), "missing.csv", "工号、部门"),
        (csv_bytes([["工号", "编号", "姓名", "部门"]]), "duplicate.csv", "多个列"),
    ],
)
def test_file_and_header_errors(content: bytes, filename: str, message: str) -> None:
    result = parse_roster(content, filename)
    assert result.has_errors
    assert message in result.issues[0].message
    assert not result.rows


@pytest.mark.parametrize(
    "row, column, message",
    [
        (["", "李宁", "研发"], "工号", "不能为空"),
        (["E1", "", "研发"], "姓名", "不能为空"),
        (["E1", "李宁", " ／ / "], "部门", "不能为空"),
        (
            ["E1", "李宁", "研发", "", "E099", "one@example.com"],
            "上级工号",
            "名单里没有工号 E099",
        ),
        (
            ["E1", "李宁", "研发", "", "e1", "one@example.com"],
            "上级工号",
            "上级不能是自己",
        ),
        (["E1", "李宁", "研发", "", "", "bad@"], "邮箱", "邮箱格式不正确"),
    ],
)
def test_row_errors(row: list[object], column: str, message: str) -> None:
    result = parse_roster(csv_bytes([list(HEADERS), row]), "名单.csv")
    assert result.has_errors
    issue = result.issues[0]
    assert issue.row_number == 2
    assert issue.column == column
    assert issue.message.startswith(f"第 2 行「{column}」：{message}")


def test_case_insensitive_duplicates_and_manager_lookup() -> None:
    result = parse_roster(
        csv_bytes(
            [
                list(HEADERS),
                ["E1", "甲", "研发", "", "", "One@example.com"],
                ["e1", "乙", "研发", "", "", "one@EXAMPLE.com"],
                ["E2", "丙", "研发", "", "e1", "two@example.com"],
            ]
        ),
        "名单.csv",
    )
    assert len(result.issues) == 2
    assert all(issue.row_number == 3 for issue in result.issues)
    assert "工号重复" in result.issues[0].message
    assert "邮箱重复" in result.issues[1].message


@pytest.mark.parametrize("size, cyclic", [(3, True), (5000, True), (5000, False)])
@pytest.mark.parametrize("xlsx", [False, True])
@pytest.mark.roster_performance
def test_long_chains_cycles_and_performance(
    size: int, cyclic: bool, xlsx: bool
) -> None:
    rows: list[list[object]] = [list(HEADERS)]
    for index in range(size):
        manager = f"E{index + 1}" if index < size - 1 else ("e0" if cyclic else "")
        rows.append(
            [
                f"E{index}",
                "员工",
                "公司/研发/平台",
                "工程师",
                manager,
                f"e{index}@example.com",
            ]
        )
    content = xlsx_bytes(rows) if xlsx else csv_bytes(rows)
    started = perf_counter()
    result = parse_roster(content, "名单.xlsx" if xlsx else "名单.csv")
    elapsed = perf_counter() - started
    assert elapsed < 2, f"解析 {size} 行耗时 {elapsed:.3f} 秒"
    assert len(result.rows) == size
    assert result.has_errors == cyclic
    if cyclic:
        assert len(result.issues) == 1
        assert "汇报关系成环：E0 → E1" in result.issues[0].message
        assert f"E{size - 1} → E0" in result.issues[0].message


def test_multiple_cycles_and_feeder_chain() -> None:
    result = parse_roster(
        csv_bytes(
            [
                list(HEADERS),
                ["A", "甲", "研发", "", "B", "a@example.com"],
                ["B", "乙", "研发", "", "C", "b@example.com"],
                ["C", "丙", "研发", "", "B", "c@example.com"],
                ["D", "丁", "研发", "", "E", "d@example.com"],
                ["E", "戊", "研发", "", "D", "e@example.com"],
            ]
        ),
        "名单.csv",
    )
    assert len(result.issues) == 2
    assert "B → C → B" in result.issues[0].message
    assert "D → E → D" in result.issues[1].message


def test_row_limit_counts_people_not_blank_rows() -> None:
    rows: list[list[object]] = [list(HEADERS), []]
    rows.extend(
        [[f"E{i}", "员工", "研发", "", "", f"e{i}@example.com"] for i in range(5001)]
    )
    result = parse_roster(csv_bytes(rows), "名单.csv")
    assert len(result.rows) == 5000
    assert result.issues[0].row_number == 5003
    assert "超过 5000 行" in result.issues[0].message


def test_xlsx_numeric_codes_and_unicode() -> None:
    result = parse_roster(
        xlsx_bytes(
            [
                [],
                list(HEADERS),
                [1001.0, "䶮㵘𤭢", "公司／研发/平台", "", "", "one@example.com"],
                ["001002", "Alex", "公司/运营/平台", "", 1001, "two@example.com"],
            ]
        ),
        "名单.xlsx",
    )
    assert not result.has_errors
    assert result.rows[0].row_number == 3
    assert result.rows[0].employee_code == "1001"
    assert result.rows[0].display_name == "䶮㵘𤭢"
    assert result.rows[1].employee_code == "001002"
    assert result.rows[1].manager_code == "1001"
    assert len(result.issues) == 2
    assert all("工号是数字格式" in issue.message for issue in result.issues)


@pytest.mark.parametrize(
    "rows, column",
    [
        ([["=1+1"]], "第 1 列"),
        (
            [list(HEADERS), ["E1", "李宁", "研发", "=1+1", "", "one@example.com"]],
            "职位",
        ),
        ([list(HEADERS), [None, None, None, "=1+1"]], "职位"),
    ],
)
def test_formula_without_cached_value(rows: list[list[object]], column: str) -> None:
    result = parse_roster(xlsx_bytes(rows), "公式.xlsx")
    assert result.has_errors
    assert result.issues[0].column == column
    assert "公式没有计算结果" in result.issues[0].message


def test_formula_with_cached_value() -> None:
    original = xlsx_bytes(
        [list(HEADERS), ["E1", "李宁", "研发", "=1+1", "", "one@example.com"]]
    )
    output = BytesIO()
    with (
        ZipFile(BytesIO(original)) as source,
        ZipFile(output, "w", ZIP_DEFLATED) as target,
    ):
        for name in source.namelist():
            data = source.read(name)
            if name == "xl/worksheets/sheet1.xml":
                root = ElementTree.fromstring(data)
                ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
                for cell in root.iter(f"{ns}c"):
                    if cell.find(f"{ns}f") is not None:
                        cached = cell.find(f"{ns}v")
                        assert cached is not None
                        cached.text = "2"
                data = ElementTree.tostring(root)
            target.writestr(name, data)
    result = parse_roster(output.getvalue(), "公式.xlsx")
    assert not result.has_errors
    assert result.rows[0].title == "2"


def test_sample_roster() -> None:
    content = (
        Path(__file__).resolve().parents[4] / "examples/roster/sample-30.csv"
    ).read_bytes()
    assert content.startswith(b"\xef\xbb\xbf")
    result = parse_roster(content, "sample-30.csv")
    assert len(result.rows) == 30
    assert not result.issues
    assert sum(row.email is None for row in result.rows) == 0
    assert sum(row.manager_code is None for row in result.rows) == 1
    assert all(len(row.department_path) >= 3 for row in result.rows)
    assert any("𤭢" in row.display_name for row in result.rows)
    groups = {
        row.department_path
        for row in result.rows
        if row.department_path[-1] == "质量组"
    }
    assert len(groups) == 2
