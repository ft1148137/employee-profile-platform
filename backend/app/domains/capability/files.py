from io import BytesIO
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, ZipFile

import yaml
from openpyxl import Workbook
from openpyxl.utils.exceptions import InvalidFileException
from pydantic import ValidationError

from app.domains.capability.schemas import DictionaryDefinition
from app.domains.org.roster.parser import read_xlsx_rows

HEADERS = (
    "能力 key",
    "名称",
    "分类",
    "说明",
    "等级 key",
    "等级名称",
    "等级描述",
    "允许 AI 推断",
)


def parse_dictionary(
    content: bytes, filename: str
) -> tuple[list[DictionaryDefinition], list[str]]:
    definitions: list[DictionaryDefinition] = []
    errors: list[str] = []
    rows: list[tuple[int, object]] = []
    try:
        if Path(filename).suffix.lower() in (".yaml", ".yml"):
            data = yaml.safe_load(content)
            node = yaml.compose(content)
            if not isinstance(data, list) or not isinstance(node, yaml.SequenceNode):
                return [], ["第 1 行：词典必须是能力列表，请下载模板填写"]
            rows = [
                (n.start_mark.line + 1, value)
                for n, value in zip(node.value, data, strict=True)
            ]
        elif Path(filename).suffix.lower() == ".xlsx":
            with ZipFile(BytesIO(content)) as archive:
                if (
                    sum(info.file_size for info in archive.infolist())
                    > 20 * 1024 * 1024
                ):
                    return [], ["第 1 行：Excel 解压后超过 20 MB"]
            groups: dict[str, dict[str, Any]] = {}
            for number, values, missing in read_xlsx_rows(content):
                if number > 20001:
                    return [], [f"第 {number} 行：Excel 最多支持 20000 行等级"]
                if number == 1:
                    if tuple(values) != HEADERS:
                        return [], ["第 1 行：列名不正确，请下载 Excel 模板填写"]
                    continue
                if not any(v is not None for v in values):
                    continue
                if missing:
                    errors.append(f"第 {number} 行：公式没有计算结果，请粘贴为值")
                values += [None] * (len(HEADERS) - len(values))
                key, name, category, description, level, label, detail, ai = values[:8]
                ai = True if ai is None else ai
                if ai in ("false", "否"):
                    ai = False
                elif ai in ("true", "是"):
                    ai = True
                raw: dict[str, Any] = {
                    "key": key,
                    "name": name,
                    "category": category or "",
                    "description": description or "",
                    "ai_inference": ai,
                }
                group = groups.get(str(key))
                if group is None:
                    group = {**raw, "level_rubric": {"levels": []}}
                    groups[str(key)] = group
                    rows.append((number, group))
                elif any(group[k] != v for k, v in raw.items()):
                    errors.append(
                        f"第 {number} 行：同一能力 key 的名称、分类、说明或 AI 设置不一致"
                    )
                if any(v["key"] == level for v in group["level_rubric"]["levels"]):
                    errors.append(f"第 {number} 行：等级 key 重复")
                group["level_rubric"]["levels"].append(
                    {"key": level, "label": label, "description": detail}
                )
        else:
            return [], ["第 1 行：请上传 YAML 或 .xlsx 文件"]
        if not rows or len(rows) > 1000:
            return [], ["第 1 行：词典需包含 1 至 1000 项能力"]
        seen: set[str] = set()
        for number, row in rows:
            try:
                definition = DictionaryDefinition.model_validate(row)
                if definition.key in seen:
                    errors.append(f"第 {number} 行：能力 key 重复：{definition.key}")
                seen.add(definition.key)
                definitions.append(definition)
            except ValidationError as error:
                for issue in error.errors():
                    field = " / ".join(str(v) for v in issue["loc"])
                    message = (
                        "等级 key 重复"
                        if "等级 key 重复" in issue["msg"]
                        else "缺少必填值或格式不正确"
                    )
                    errors.append(f"第 {number} 行「{field}」：{message}")
    except yaml.MarkedYAMLError as error:
        errors.append(
            f"第 {(error.problem_mark.line + 1) if error.problem_mark else 1} 行：YAML 格式不正确"
        )
    except (
        UnicodeError,
        yaml.YAMLError,
        BadZipFile,
        InvalidFileException,
        ParseError,
        KeyError,
        IndexError,
        ValueError,
        OSError,
    ):
        errors.append("第 1 行：文件无法读取，请检查格式或重新下载模板")
    return definitions, errors


def dictionary_yaml(definitions: list[DictionaryDefinition]) -> bytes:
    return yaml.safe_dump(
        [v.model_dump(mode="json") for v in definitions],
        allow_unicode=True,
        sort_keys=False,
    ).encode()


def dictionary_xlsx(definitions: list[DictionaryDefinition]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(HEADERS)
    for definition in definitions:
        for level in definition.level_rubric.levels:
            sheet.append(
                (
                    definition.key,
                    definition.name,
                    definition.category,
                    definition.description,
                    level.key,
                    level.label,
                    level.description,
                    definition.ai_inference,
                )
            )
    sheet.freeze_panes = "A2"
    result = BytesIO()
    workbook.save(result)
    workbook.close()
    return result.getvalue()
