from uuid import UUID

from fastapi import HTTPException
from sqlmodel import Session, col, select

from app.domains.capability.models import (
    CapabilityDefinition,
    CapabilitySchemaVersion,
    SchemaStatus,
)


class SchemaNotFound(HTTPException):
    def __init__(self) -> None:
        super().__init__(404, "Capability schema not found")


def get_schema(
    session: Session, org_id: UUID, schema_id: UUID | None = None
) -> CapabilitySchemaVersion:
    query = select(CapabilitySchemaVersion).where(
        CapabilitySchemaVersion.org_id == org_id
    )
    schema = session.exec(
        query.where(
            CapabilitySchemaVersion.id == schema_id
            if schema_id
            else CapabilitySchemaVersion.status == SchemaStatus.ACTIVE
        )
    ).first()
    if schema is None:
        raise SchemaNotFound()
    return schema


def list_capabilities(
    session: Session, org_id: UUID, schema_id: UUID
) -> list[CapabilityDefinition]:
    return list(
        session.exec(
            select(CapabilityDefinition)
            .where(
                CapabilityDefinition.org_id == org_id,
                CapabilityDefinition.schema_version_id == schema_id,
            )
            .order_by(col(CapabilityDefinition.position), col(CapabilityDefinition.id))
        ).all()
    )
