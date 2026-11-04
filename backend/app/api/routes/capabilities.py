from typing import Literal

from fastapi import APIRouter, HTTPException, Response, UploadFile

from app.api.deps import SessionDep
from app.authz.deps import OrgAdminDep, OrgPrincipalDep
from app.domains.capability.files import (
    dictionary_xlsx,
    dictionary_yaml,
    parse_dictionary,
)
from app.domains.capability.models import CapabilitySchemaVersion
from app.domains.capability.schemas import (
    ActiveSchemaPublic,
    CapabilitiesPublic,
    CapabilityPublic,
    DictionaryDefinition,
    DictionaryPreview,
    SaveDictionary,
)
from app.domains.capability.service import (
    DictionaryConflict,
    SchemaNotFound,
    get_schema,
    list_capabilities,
    preview_version,
    save_version,
)
from app.domains.capability.templates import get_template
from app.domains.drafting.models import DraftingSetting
from app.domains.drafting.service import queue_drafting

router = APIRouter(prefix="/orgs/{org_id}/capabilities", tags=["capabilities"])


@router.get("", response_model=CapabilitiesPublic)
def read_capabilities(
    session: SessionDep, principal: OrgPrincipalDep
) -> CapabilitiesPublic:
    schema = get_schema(session, principal.org_id)
    return CapabilitiesPublic(
        schema_version=ActiveSchemaPublic.model_validate(schema),
        data=[
            CapabilityPublic.model_validate(c)
            for c in list_capabilities(session, principal.org_id, schema.id)
        ],
    )


def _definitions(
    session: SessionDep,
    principal: OrgPrincipalDep,
    schema: CapabilitySchemaVersion | None = None,
) -> list[DictionaryDefinition]:
    schema = schema or get_schema(session, principal.org_id)
    setting = session.get(DraftingSetting, principal.org_id)
    excluded = set(setting.excluded_capability_keys if setting else [])
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
                "ai_inference": c.key not in excluded,
            }
        )
        for c in list_capabilities(session, principal.org_id, schema.id)
    ]


@router.get("/export", response_class=Response)
def export_dictionary(session: SessionDep, principal: OrgPrincipalDep) -> Response:
    return Response(
        dictionary_yaml(_definitions(session, principal)),
        media_type="application/yaml",
        headers={"Content-Disposition": 'attachment; filename="capabilities.yaml"'},
    )


@router.get("/template", response_class=Response)
def dictionary_template(
    _principal: OrgAdminDep, format: Literal["yaml", "xlsx"] = "yaml"
) -> Response:
    definitions = [
        DictionaryDefinition.model_validate(c.model_dump())
        for c in get_template("tech-rnd").capabilities[:1]
    ]
    return Response(
        dictionary_yaml(definitions)
        if format == "yaml"
        else dictionary_xlsx(definitions),
        media_type="application/yaml"
        if format == "yaml"
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="capabilities-template.{format}"'
        },
    )


@router.post("/preview", response_model=DictionaryPreview)
def preview_dictionary(
    file: UploadFile, session: SessionDep, principal: OrgAdminDep
) -> DictionaryPreview:
    content = file.file.read(2 * 1024 * 1024 + 1)
    if len(content) > 2 * 1024 * 1024:
        raise DictionaryTooLarge()
    definitions, errors = parse_dictionary(content, file.filename or "")
    return preview_version(session, principal, definitions, errors)


@router.post("/versions", response_model=ActiveSchemaPublic)
def save_dictionary(
    body: SaveDictionary, session: SessionDep, principal: OrgAdminDep
) -> ActiveSchemaPublic:
    return _save_dictionary(body, session, principal, sync_ai_inference=False)


@router.get("/editor", response_model=DictionaryPreview)
def read_dictionary_editor(
    session: SessionDep, principal: OrgAdminDep
) -> DictionaryPreview:
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


@router.post("/editor/preview", response_model=DictionaryPreview)
def preview_dictionary_editor(
    body: SaveDictionary, session: SessionDep, principal: OrgAdminDep
) -> DictionaryPreview:
    preview = preview_version(
        session, principal, body.definitions, [], sync_ai_inference=True
    )
    if preview.expected_version != body.expected_version:
        raise DictionaryConflict()
    return preview


@router.put("", response_model=ActiveSchemaPublic)
def update_dictionary(
    body: SaveDictionary, session: SessionDep, principal: OrgAdminDep
) -> ActiveSchemaPublic:
    return _save_dictionary(body, session, principal, sync_ai_inference=True)


def _save_dictionary(
    body: SaveDictionary,
    session: SessionDep,
    principal: OrgAdminDep,
    *,
    sync_ai_inference: bool,
) -> ActiveSchemaPublic:
    schema = save_version(
        session,
        principal,
        body.definitions,
        expected_version=body.expected_version,
        sync_ai_inference=sync_ai_inference,
    )
    result = ActiveSchemaPublic.model_validate(schema)
    if body.redraft:
        queue_drafting(session, principal.org_id, "all", pending_owner_only=True)
    else:
        session.commit()
    return result


class DictionaryTooLarge(HTTPException):
    def __init__(self) -> None:
        super().__init__(413, "文件超过 2 MB，请缩小后上传")
