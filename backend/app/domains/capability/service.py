from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func
from sqlmodel import Session, col, select

from app.authz.principal import Principal
from app.domains.audit.service import record_audit
from app.domains.capability.models import (
    CapabilityDefinition,
    CapabilitySchemaVersion,
    SchemaStatus,
)
from app.domains.capability.templates import get_template
from app.domains.org.models import Organization
from app.domains.profile.models import ProfileClaim


class SchemaHasClaims(HTTPException):
    def __init__(self) -> None:
        super().__init__(409, "已经有画像判断，不能直接更换词典")


def activate_template(
    session: Session, principal: Principal, template_id: str
) -> CapabilitySchemaVersion:
    template = get_template(template_id)
    org_id = principal.org_id
    # Serialize activations even when this organization has no schema yet.
    session.exec(
        select(Organization).where(Organization.id == org_id).with_for_update()
    ).one()
    if session.exec(
        select(ProfileClaim.id).where(ProfileClaim.org_id == org_id)
    ).first():
        raise SchemaHasClaims()
    active = session.exec(
        select(CapabilitySchemaVersion).where(
            CapabilitySchemaVersion.org_id == org_id,
            CapabilitySchemaVersion.status == SchemaStatus.ACTIVE,
        )
    ).first()
    if active and active.template_ref == template.template_ref:
        return active
    version = (
        session.exec(
            select(func.max(CapabilitySchemaVersion.version)).where(
                CapabilitySchemaVersion.org_id == org_id
            )
        ).one()
        or 0
    )
    if active:
        active.status = SchemaStatus.RETIRED
        session.add(active)
        session.flush()
    schema = CapabilitySchemaVersion(
        org_id=org_id,
        version=version + 1,
        status=SchemaStatus.ACTIVE,
        template_ref=template.template_ref,
        activated_at=datetime.now(UTC),
    )
    session.add(schema)
    session.flush()
    for position, capability in enumerate(template.capabilities):
        session.add(
            CapabilityDefinition(
                org_id=org_id,
                schema_version_id=schema.id,
                position=position,
                **capability.model_dump(mode="json"),
            )
        )
    record_audit(
        session,
        org_id=org_id,
        actor_type=principal.actor_type,
        actor_id=str(principal.user_id),
        action="capability_schema.activated",
        target_type="capability_schema_version",
        target_id=str(schema.id),
        details={
            "template_id": template_id,
            "template_ref": template.template_ref,
            "schema_version_id": str(schema.id),
            "capability_count": len(template.capabilities),
        },
    )
    return schema


class SchemaNotFound(HTTPException):
    def __init__(self) -> None:
        super().__init__(404, "能力词典不存在")


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
