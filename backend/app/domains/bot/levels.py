from pydantic import BaseModel, JsonValue
from sqlmodel import Session

from app.authz.principal import Principal
from app.domains.bot.schemas import Dictionary
from app.domains.change_request import employee


def name_levels[T: BaseModel](db: Session, p: Principal, result: T) -> T:
    labels: dict[tuple[str, str], str] | None = None

    def visit(value: JsonValue, key: str = "") -> None:
        nonlocal labels
        if isinstance(value, list):
            for item in value:
                visit(item, key)
        elif isinstance(value, dict):
            capability = value.get("capability")
            after = value.get("after")
            if not isinstance(capability, dict) and isinstance(after, dict):
                capability = after.get("capability")
            candidate = (
                capability.get("key")
                if isinstance(capability, dict)
                else value.get("capability_key")
            )
            if isinstance(candidate, str):
                key = candidate
            if "assessment_status" in value and "level" in value:
                level = value["level"]
                value["level_name"] = "未定级"
                if isinstance(level, str):
                    if labels is None:
                        dictionary = Dictionary.model_validate(
                            employee.capability_dictionary(p, db)
                        )
                        labels = {
                            (c.key, rubric_level.key): rubric_level.label
                            for c in dictionary.data
                            if c.level_rubric
                            for rubric_level in c.level_rubric.levels
                        }
                    value["level_name"] = labels.get((key, level), "等级暂不可用")
            for item in value.values():
                visit(item, key)

    data = result.model_dump(mode="json")
    visit(data)
    return type(result).model_validate(data)
