from datetime import datetime
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictBool,
    model_validator,
)
from sqlmodel import SQLModel

from app.seed.schema import RubricLevel


class SchemaVersionPublic(SQLModel):
    id: UUID
    version: int
    template_ref: str | None


class ActiveSchemaPublic(SchemaVersionPublic):
    activated_at: datetime | None


class CapabilityPublic(SQLModel):
    id: UUID
    key: str
    name: str
    category: str | None
    description: str | None
    level_rubric: dict[str, JsonValue] | None


class CapabilitiesPublic(SQLModel):
    schema_version: ActiveSchemaPublic
    data: list[CapabilityPublic]


class CapabilityTemplatePublic(SQLModel):
    id: str
    name: str
    industry: str
    description: str
    capability_count: int
    categories: list[str]
    recommended: bool


class TemplateCapabilityPublic(SQLModel):
    key: str
    name: str
    category: str
    description: str
    levels: list[RubricLevel]


class CapabilityTemplatePreview(CapabilityTemplatePublic):
    capabilities: list[TemplateCapabilityPublic]


class ActivateCapabilitySchema(SQLModel):
    template_id: str


class DictionaryLevel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    key: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=4000)


class DictionaryRubric(BaseModel):
    model_config = ConfigDict(extra="forbid")
    levels: list[DictionaryLevel] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique_levels(self) -> DictionaryRubric:
        if len({v.key for v in self.levels}) != len(self.levels):
            raise ValueError("等级 key 重复")
        return self


class DictionaryDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    key: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    category: str = Field(max_length=200)
    description: str = Field(max_length=4000)
    level_rubric: DictionaryRubric
    ai_inference: StrictBool = True


class SaveDictionary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    definitions: list[DictionaryDefinition] = Field(min_length=1, max_length=1000)
    expected_version: int = Field(ge=0)
    redraft: bool = False

    @model_validator(mode="after")
    def unique_keys(self) -> SaveDictionary:
        if len({v.key for v in self.definitions}) != len(self.definitions):
            raise ValueError("能力 key 重复")
        return self


class DictionaryPreview(BaseModel):
    expected_version: int
    definitions: list[DictionaryDefinition]
    added: list[str]
    modified: list[str]
    removed: list[str]
    errors: list[str]
