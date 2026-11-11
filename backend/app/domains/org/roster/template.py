import csv
from io import BytesIO, StringIO

from openpyxl import Workbook

from app.domains.org.employee_fields import FIELD_LABELS
from app.domains.org.roster.schemas import HEADERS

TEMPLATE_HEADERS = (*HEADERS, *(f"{label}（可选）" for label in FIELD_LABELS.values()))

EXAMPLE_ROWS = (
    ("E001", "欧阳宁", "示例公司/管理中心/总经办", "负责人", "", "ning@example.com"),
    (
        "E002",
        "李𤭢",
        "示例公司/研发中心/平台研发部",
        "工程师",
        "E001",
        "li@example.com",
    ),
)
INSTRUCTIONS = (
    "工号：必填，每人唯一；请使用文本格式，以免丢失开头的 0。",
    "姓名：必填，填写员工的姓名。",
    "部门：必填，按层级用 / 分隔，例如：公司/研发中心/平台研发部。",
    "职位：选填，填写员工的岗位或职务。",
    "上级工号：填写名单内直属上级的工号，最高负责人可以留空，不能填自己或形成循环。",
    "邮箱：必填，作为登录名；必须是有效且不重复的邮箱。",
    "岗位序列、工作地点、入职日期：可选，须由企业管理员开启；日期填写 YYYY-MM-DD。缺列保留原值，空单元格清空。",
    "名单按全量导入，名单里没有的在职员工会被停用。",
    "只填写第一个工作表，删除示例数据后填写；最多 5000 人。",
)


def build_template_xlsx() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "员工名单"
    for number, values in enumerate(
        (TEMPLATE_HEADERS, *(tuple(row) + ("", "", "") for row in EXAMPLE_ROWS)), 1
    ):
        for column, value in enumerate(values, 1):
            cell = sheet.cell(number, column, value)
            cell.data_type = "s"
            cell.number_format = "@"
    sheet.freeze_panes = "A2"
    sheet.column_dimensions["A"].number_format = "@"
    sheet.column_dimensions["E"].number_format = "@"
    for letter, width in zip(
        "ABCDEFGHI", (18, 18, 45, 20, 18, 32, 24, 24, 24), strict=True
    ):
        sheet.column_dimensions[letter].width = width
    instructions = workbook.create_sheet("填写说明")
    instructions.column_dimensions["A"].width = 110
    for number, text in enumerate(INSTRUCTIONS, 1):
        cell = instructions.cell(number, 1, text)
        cell.data_type = "s"
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def build_template_csv() -> bytes:
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(TEMPLATE_HEADERS)
    writer.writerows(tuple(row) + ("", "", "") for row in EXAMPLE_ROWS)
    return output.getvalue().encode("utf-8-sig")
