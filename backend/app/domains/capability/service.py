from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from pydantic import JsonValue
from sqlalchemy import func
from sqlmodel import Session, col, delete, select

from app.authz.principal import Principal
from app.domains.audit.service import record_audit
from app.domains.capability.models import (
    CapabilityDefinition,
    CapabilitySchemaVersion,
    SchemaStatus,
)
from app.domains.capability.schemas import (
    DictionaryDefinition,
    DictionaryPreview,
    SaveDictionary,
)
from app.domains.capability.templates import get_template
from app.domains.change_request.models import (
    ChangeItem,
    ChangeItemEvidence,
    ProfileChangeRequest,
    RequestStatus,
)
from app.domains.inbox.models import InboxKind
from app.domains.inbox.service import notify
from app.domains.org.models import Organization
from app.domains.profile.models import ProfileClaim


def activate_template(
    session: Session, principal: Principal, template_id: str
) -> CapabilitySchemaVersion:
    template = get_template(template_id)
    session.exec(
        select(Organization)
        .where(Organization.id == principal.org_id)
        .with_for_update()
    ).one()
    active = session.exec(
        select(CapabilitySchemaVersion).where(
            CapabilitySchemaVersion.org_id == principal.org_id,
            CapabilitySchemaVersion.status == SchemaStatus.ACTIVE,
        )
    ).first()
    if active and active.template_ref == template.template_ref:
        return active
    return save_version(
        session,
        principal,
        [
            DictionaryDefinition.model_validate(c.model_dump())
            for c in template.capabilities
        ],
        template_ref=template.template_ref,
    )


def preview_version(
    session: Session,
    principal: Principal,
    definitions: list[DictionaryDefinition],
    errors: list[str],
    *,
    warnings: list[str] | None = None,
) -> DictionaryPreview:
    try:
        active = get_schema(session, principal.org_id)
    except SchemaNotFound:
        active = None
    previous = (
        {c.key: c for c in list_capabilities(session, principal.org_id, active.id)}
        if active
        else {}
    )
    incoming = {c.key: c for c in definitions}
    removed = previous.keys() - incoming.keys()
    claim_counts = dict(
        session.exec(
            select(CapabilityDefinition.key, func.count(col(ProfileClaim.id)))
            .join(
                ProfileClaim,
                col(ProfileClaim.capability_definition_id) == CapabilityDefinition.id,
            )
            .where(
                CapabilityDefinition.org_id == principal.org_id,
                ProfileClaim.org_id == principal.org_id,
                col(CapabilityDefinition.key).in_(removed),
            )
            .group_by(CapabilityDefinition.key)
        ).all()
    )
    item_counts = dict(
        session.exec(
            select(CapabilityDefinition.key, func.count(col(ChangeItem.id)))
            .join(
                ChangeItem,
                col(ChangeItem.capability_definition_id) == CapabilityDefinition.id,
            )
            .join(
                ProfileChangeRequest,
                col(ChangeItem.request_id) == ProfileChangeRequest.id,
            )
            .where(
                CapabilityDefinition.org_id == principal.org_id,
                ChangeItem.org_id == principal.org_id,
                ProfileChangeRequest.org_id == principal.org_id,
                col(CapabilityDefinition.key).in_(removed),
                col(ProfileChangeRequest.status).in_(
                    [RequestStatus.DRAFT, RequestStatus.PENDING_OWNER]
                ),
            )
            .group_by(CapabilityDefinition.key)
        ).all()
    )
    return DictionaryPreview(
        expected_version=active.version if active else 0,
        definitions=definitions,
        added=sorted(incoming.keys() - previous.keys()),
        removed=sorted(removed),
        removed_claim_counts={k: claim_counts.get(k, 0) for k in removed},
        removed_item_counts={k: item_counts.get(k, 0) for k in removed},
        modified=sorted(
            k
            for k in incoming.keys() & previous.keys()
            if incoming[k].model_dump()
            != {
                f: getattr(previous[k], f)
                for f in ("key", "name", "category", "description", "level_rubric")
            }
        ),
        errors=errors,
        warnings=warnings or [],
    )


def save_version(
    session: Session,
    principal: Principal,
    definitions: list[DictionaryDefinition],
    *,
    expected_version: int | None = None,
    template_ref: str | None = None,
) -> CapabilitySchemaVersion:
    SaveDictionary(definitions=definitions, expected_version=expected_version or 0)
    org_id = principal.org_id
    # Serialize saves, including the first version for an organization.
    session.exec(
        select(Organization).where(Organization.id == org_id).with_for_update()
    ).one()
    version = (
        session.exec(
            select(func.max(CapabilitySchemaVersion.version)).where(
                CapabilitySchemaVersion.org_id == org_id
            )
        ).one()
        or 0
    )
    if expected_version is not None and version != expected_version:
        raise DictionaryConflict()
    active = session.exec(
        select(CapabilitySchemaVersion).where(
            CapabilitySchemaVersion.org_id == org_id,
            CapabilitySchemaVersion.status == SchemaStatus.ACTIVE,
        )
    ).first()
    if active:
        active.status = SchemaStatus.RETIRED
        session.flush()
    schema = CapabilitySchemaVersion(
        org_id=org_id,
        version=version + 1,
        status=SchemaStatus.ACTIVE,
        template_ref=template_ref,
        activated_at=datetime.now(UTC),
    )
    session.add(schema)
    session.flush()
    new = {}
    for position, definition in enumerate(definitions):
        capability = CapabilityDefinition(
            org_id=org_id,
            schema_version_id=schema.id,
            position=position,
            **definition.model_dump(mode="json"),
        )
        session.add(capability)
        new[definition.key] = capability
    session.flush()
    _remap_requests(session, principal, new)
    details: dict[str, JsonValue] = {
        "template_ref": template_ref,
        "schema_version_id": str(schema.id),
        "capability_count": len(definitions),
    }
    if template_ref:
        details["template_id"] = template_ref.split("@")[0]
    record_audit(
        session,
        org_id=org_id,
        actor_type=principal.actor_type,
        actor_id=str(principal.user_id),
        action="capability_schema.activated",
        target_type="capability_schema_version",
        target_id=str(schema.id),
        details=details,
    )
    return schema


def _remap_requests(
    session: Session, principal: Principal, definitions: dict[str, CapabilityDefinition]
) -> None:
    requests = session.exec(
        select(ProfileChangeRequest)
        .where(
            ProfileChangeRequest.org_id == principal.org_id,
            col(ProfileChangeRequest.status).in_(
                [RequestStatus.DRAFT, RequestStatus.PENDING_OWNER]
            ),
        )
        .order_by(col(ProfileChangeRequest.id))
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    for request in requests:
        rows = session.exec(
            select(ChangeItem, CapabilityDefinition.key)
            .join(
                CapabilityDefinition,
                (col(CapabilityDefinition.id) == ChangeItem.capability_definition_id)
                & (col(CapabilityDefinition.org_id) == ChangeItem.org_id),
            )
            .where(
                ChangeItem.org_id == principal.org_id,
                ChangeItem.request_id == request.id,
            )
            .order_by(col(ChangeItem.position))
        ).all()
        removed, adjusted = [], []
        for item, key in rows:
            capability = definitions.get(key)
            if capability is None:
                session.exec(
                    delete(ChangeItemEvidence).where(
                        col(ChangeItemEvidence.org_id) == principal.org_id,
                        col(ChangeItemEvidence.item_id) == item.id,
                    )
                )
                session.delete(item)
                removed.append(key)
            else:
                item.capability_definition_id = capability.id
                if item.after and not level_exists(
                    capability.level_rubric, item.after.get("level")
                ):
                    adjusted.append(key)
        if not rows:
            continue
        request.revision += 1
        request.updated_at = datetime.now(UTC)
        parts = ([f"{len(removed)} 项已移除"] if removed else []) + (
            [f"{len(adjusted)} 项的等级需要重新选择"] if adjusted else []
        )
        notify(
            session,
            request,
            InboxKind.DICTIONARY_CHANGED,
            "词典调整后，" + "；".join(parts)
            if parts
            else "能力词典已更新，请重新预览后提交",
        )
        record_audit(
            session,
            org_id=principal.org_id,
            actor_type=principal.actor_type,
            actor_id=str(principal.user_id),
            action="change_request.dictionary_changed",
            target_type="profile_change_request",
            target_id=str(request.id),
            details={
                "removed_keys": [*removed],
                "adjusted_level_keys": [*adjusted],
                "revision": request.revision,
            },
        )
    session.flush()


def level_exists(rubric: dict[str, JsonValue] | None, level: object) -> bool:
    levels = (rubric or {}).get("levels", [])
    return level is None or (
        isinstance(levels, list)
        and any(isinstance(v, dict) and v.get("key") == level for v in levels)
    )


class DictionaryConflict(HTTPException):
    def __init__(self) -> None:
        super().__init__(409, "词典已被别人修改，请刷新")


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


def _definitions(
    session: Session,
    principal: Principal,
    schema: CapabilitySchemaVersion | None = None,
) -> list[DictionaryDefinition]:
    schema = schema or get_schema(session, principal.org_id)
    return [
        DictionaryDefinition.model_validate(
            {
                **c.model_dump(
                    exclude={
                        "id",
                        "org_id",
                        "created_at",
                        "schema_version_id",
                        "position",
                    }
                ),
            }
        )
        for c in list_capabilities(session, principal.org_id, schema.id)
    ]


def read_editor(session: Session, principal: Principal) -> DictionaryPreview:
    try:
        schema = get_schema(session, principal.org_id)
    except SchemaNotFound:
        return preview_version(session, principal, [], [])
    return DictionaryPreview(
        definitions=_definitions(session, principal, schema),
        expected_version=schema.version,
        added=[],
        modified=[],
        removed=[],
        removed_claim_counts={},
        removed_item_counts={},
        errors=[],
    )
