import csv
from io import BytesIO, StringIO

import pytest
from openpyxl import Workbook
from sqlmodel import Session, select

from app.domains.admin_assistant import roster_materials
from app.domains.org.models import Department
from app.domains.org.roster import service
from app.domains.org.roster.parser import parse_roster
from app.domains.org.roster.schemas import ApplyImport
from tests.admin_assistant.test_personnel import principal
from tests.api.routes.test_setup_steps import SetupTenant
from tests.api.routes.test_setup_steps import tenant as tenant


def roster_file(
    departments: list[tuple[str, str]],
    column: str = "上级部门",
    department_column: str = "部门",
) -> bytes:
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["工号", "姓名", department_column, column, "邮箱"])
    for index, (department, parent) in enumerate(departments):
        writer.writerow(
            [
                f"E{index}",
                f"合成员工{index}",
                department,
                parent,
                f"e{index}@example.com",
            ]
        )
    return output.getvalue().encode()


def test_twelve_departments_resolve_parents_independent_of_row_order() -> None:
    departments = (
        [(f"小组{index}", f"分部{index // 3}") for index in range(8)]
        + [(f"分部{index}", "总部") for index in range(3)]
        + [("总部", "")]
    )
    result = parse_roster(roster_file(departments), "名单.csv")
    assert not result.issues
    assert result.rows[0].department_path == ("总部", "分部0", "小组0")
    assert result.rows[7].department_path == ("总部", "分部2", "小组7")
    assert result.rows[-1].department_path == ("总部",)


@pytest.mark.parametrize("column", ["上级部门", "上级部门名称", "所属上级部门"])
@pytest.mark.parametrize("xlsx", [False, True])
def test_missing_parent_and_repeated_department(column: str, xlsx: bool) -> None:
    content = roster_file([("研发", "总部"), ("研发", "总部")], column)
    filename = "名单.csv"
    if xlsx:
        workbook = Workbook()
        sheet = workbook.active
        assert sheet is not None
        for row in csv.reader(StringIO(content.decode())):
            sheet.append(row)
        output = BytesIO()
        workbook.save(output)
        workbook.close()
        content = output.getvalue()
        filename = "名单.xlsx"
    result = parse_roster(content, filename)
    assert not result.issues
    assert all(row.department_path == ("总部", "研发") for row in result.rows)


def test_manager_alias_still_means_employee_code() -> None:
    result = parse_roster(
        roster_file([("总部", ""), ("总部", "E0")], "上级"), "名单.csv"
    )
    assert not result.issues
    assert result.rows[1].manager_code == "E0"
    assert result.rows[1].department_path == ("总部",)


@pytest.mark.parametrize(
    "departments, message, number",
    [
        ([("A", "B"), ("B", "A")], "成环", 2),
        ([("A", "A")], "成环", 2),
        ([("A", "B"), ("A", "C")], "与第 2 行不一致", 3),
        ([("A", ""), ("A", "B")], "与第 2 行不一致", 3),
    ],
)
def test_invalid_hierarchy_cannot_import(
    db: Session,
    tenant: SetupTenant,
    departments: list[tuple[str, str]],
    message: str,
    number: int,
) -> None:
    p = principal(db, tenant)
    preview = service.create_import(db, p, roster_file(departments), "名单.csv")
    assert not preview.can_apply
    error = next(i for i in preview.issues if i.severity == "error")
    assert error.row_number == number and error.column == "上级部门"
    assert f"第 {number} 行「上级部门」" in error.message
    assert message in error.message
    before = db.exec(select(Department).where(Department.org_id == p.org_id)).all()
    with pytest.raises(service.RosterError) as raised:
        service.apply_import(
            db, p, preview.id, ApplyImport(base_revision=preview.base_revision)
        )
    assert raised.value.status_code == 422
    after = db.exec(select(Department).where(Department.org_id == p.org_id)).all()
    assert [d.id for d in after] == [d.id for d in before]


@pytest.mark.parametrize(
    "department, column, expected",
    [
        ("总部／研发", "部门", ("总部", "研发")),
        ("研发", "部门路径", ("研发",)),
    ],
)
def test_department_path_takes_precedence(
    department: str, column: str, expected: tuple[str, ...]
) -> None:
    result = parse_roster(
        roster_file([(department, department)], department_column=column), "名单.csv"
    )
    assert not result.has_errors
    assert result.rows[0].department_path == expected
    assert len(result.issues) == 1
    assert result.issues[0].severity == "warning"
    assert "以部门路径为准" in result.issues[0].message


def test_import_creates_parent_departments(db: Session, tenant: SetupTenant) -> None:
    departments = [(f"分部{i}", "总部") for i in range(11)] + [("总部", "")]
    p = principal(db, tenant)
    preview = service.create_import(db, p, roster_file(departments), "名单.csv")
    assert preview.can_apply
    assert len(preview.created_departments) == 12
    result = service.apply_import(
        db, p, preview.id, ApplyImport(base_revision=preview.base_revision)
    )
    assert result.status == "applied"
    saved = {
        d.name: d
        for d in db.exec(select(Department).where(Department.org_id == p.org_id)).all()
    }
    assert saved["总部"].parent_department_id is None
    assert all(
        saved[f"分部{i}"].parent_department_id == saved["总部"].id for i in range(11)
    )


@pytest.mark.parametrize("hierarchy", [False, True])
def test_confirmation_card_explains_department_structure(
    db: Session, tenant: SetupTenant, hierarchy: bool
) -> None:
    args = roster_materials.MaterialArgs.model_validate(
        {
            "materials": [{"label": "合成人员名单", "attachment_id": None}],
            "rows": [
                {
                    "action": "onboard",
                    "employee_code": f"E{i}",
                    "display_name": f"合成员工{i}",
                    "department": f"总部/分部{i}" if hierarchy else f"分部{i}",
                    "email": f"e{i}@example.com",
                }
                for i in range(2)
            ],
        }
    )
    text = roster_materials.impact(db, principal(db, tenant), args).model_dump()["text"]
    if hierarchy:
        assert "总部/分部0 → 上级 总部" in text
        assert "总部/分部1 → 上级 总部" in text
        assert "都会建在第一层" not in text
    else:
        assert "所有部门都会建在第一层，没有上下级" in text
