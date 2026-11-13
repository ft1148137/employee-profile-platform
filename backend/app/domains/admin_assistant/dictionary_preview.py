"""Freeze readable dictionary changes alongside the confirmation snapshot."""

from pydantic import BaseModel, Field

from app.domains.capability.schemas import DictionaryDefinition


class CapabilityChange(DictionaryDefinition):
    changed_fields: list[str] = Field(default_factory=list)


class DictionaryChanges(BaseModel):
    added: list[CapabilityChange] = Field(default_factory=list)
    modified: list[CapabilityChange] = Field(default_factory=list)
    removed: list[CapabilityChange] = Field(default_factory=list)


def compare_dictionary(
    previous: list[DictionaryDefinition], incoming: list[DictionaryDefinition]
) -> DictionaryChanges:
    before = {d.key: d for d in previous}
    after = {d.key: d for d in incoming}
    changes = DictionaryChanges()
    for definition in incoming:
        old = before.get(definition.key)
        fields = [
            label
            for field, label in (
                ("name", "名称"),
                ("category", "分类"),
                ("description", "说明"),
                ("level_rubric", "等级"),
            )
            if old is not None and getattr(old, field) != getattr(definition, field)
        ]
        change = CapabilityChange(**definition.model_dump(), changed_fields=fields)
        if old is None:
            changes.added.append(change)
        elif fields:
            changes.modified.append(change)
    changes.removed = [
        CapabilityChange(**d.model_dump()) for d in previous if d.key not in after
    ]
    return changes
