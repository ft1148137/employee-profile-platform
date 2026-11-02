from datetime import datetime
from uuid import UUID

from pydantic import JsonValue
from sqlmodel import SQLModel


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
