import hashlib
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from app.core.config import settings
from app.domains.capability.schemas import CapabilityTemplatePublic
from app.seed.schema import Capability


class InvalidTemplate(ValueError):
    """A catalog or template file failed validation."""


class TemplateNotFound(HTTPException):
    def __init__(self) -> None:
        super().__init__(404, "没有这个词典模板")


class CatalogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    file: str
    name: str
    industry: str
    description: str
    recommended: bool = False


@dataclass(frozen=True)
class CapabilityTemplate:
    entry: CatalogEntry
    capabilities: list[Capability]
    template_ref: str

    def public(self) -> CapabilityTemplatePublic:
        return CapabilityTemplatePublic(
            **self.entry.model_dump(exclude={"file"}),
            capability_count=len(self.capabilities),
            categories=list(dict.fromkeys(c.category for c in self.capabilities)),
        )


def read_yaml[T](path: Path, kind: type[T]) -> tuple[T, bytes]:
    try:
        content = path.read_bytes()
        return TypeAdapter(kind).validate_python(yaml.safe_load(content)), content
    except (OSError, UnicodeError, yaml.YAMLError, ValidationError) as exc:
        raise InvalidTemplate(f"{path.name}: {exc}") from exc


@cache
def load_catalog(directory: Path) -> tuple[CapabilityTemplate, ...]:
    entries, _ = read_yaml(directory / "catalog.yaml", list[CatalogEntry])
    if len({entry.id for entry in entries}) != len(entries):
        raise InvalidTemplate("catalog.yaml: duplicate template id")
    for entry in entries:
        if (
            entry.file != f"{entry.id}.zh-CN.yaml"
            or Path(entry.file).name != entry.file
        ):
            raise InvalidTemplate(f"catalog.yaml: invalid file {entry.file}")
    unlisted = {p.name for p in directory.glob("*.zh-CN.yaml")} - {
        entry.file for entry in entries
    }
    if unlisted:
        raise InvalidTemplate(f"catalog.yaml: unlisted files {sorted(unlisted)}")
    templates = []
    for entry in entries:
        capabilities, content = read_yaml(directory / entry.file, list[Capability])
        if len({c.key for c in capabilities}) != len(capabilities):
            raise InvalidTemplate(f"{entry.file}: duplicate capability key")
        for capability in capabilities:
            levels = capability.level_rubric.levels
            if len(levels) != 3 or {level.key for level in levels} != {
                "needs_support",
                "routine",
                "independent_complex",
            }:
                raise InvalidTemplate(f"{entry.file}: {capability.key}: invalid levels")
        templates.append(
            CapabilityTemplate(
                entry,
                capabilities,
                f"{entry.id}@{hashlib.sha256(content).hexdigest()[:12]}",
            )
        )
    return tuple(sorted(templates, key=lambda template: not template.entry.recommended))


def get_template(template_id: str) -> CapabilityTemplate:
    for template in load_catalog(settings.CAPABILITY_TEMPLATES_DIR):
        if template.entry.id == template_id:
            return template
    raise TemplateNotFound()
