from io import BytesIO

from openpyxl import Workbook
from openpyxl.cell import Cell


def export_accounts(rows: list[list[str]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "账号"
    for row_index, values in enumerate(
        [["工号", "姓名", "部门", "登录地址", "初始密码", "备注"], *rows], start=1
    ):
        cells = []
        for column, value in enumerate(values, start=1):
            cell = Cell(sheet, row=row_index, column=column, value=value)
            cell.data_type = "s"
            cell.number_format = "@"
            cells.append(cell)
        sheet.append(cells)
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()
