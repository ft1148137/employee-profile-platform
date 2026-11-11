from typing import Literal

from fastapi import APIRouter, HTTPException, Response, UploadFile

from app.api.deps import SessionDep
from app.authz.deps import OrgAdminDep, OrgPrincipalDep
from app.domains.capability.files import (
    dictionary_xlsx,
    dictionary_yaml,
    parse_dictionary,
)
from app.domains.capability.publish import save_dictionary as publish_dictionary
from app.domains.capability.schemas import (
    ActiveSchemaPublic,
    CapabilitiesPublic,
    CapabilityPublic,
    CapabilityStatsPublic,
    DictionaryDefinition,
    DictionaryPreview,
    SaveDictionary,
)
from app.domains.capability.service import (
    DictionaryConflict,
    _definitions,
    get_schema,
    list_capabilities,
    preview_version,
    read_editor,
)
from app.domains.capability.stats import read_stats
from app.domains.capability.templates import get_template

router = APIRouter(prefix="/orgs/{org_id}/capabilities", tags=["capabilities"])


@router.get("/stats", response_model=CapabilityStatsPublic)
def read_capability_stats(
    session: SessionDep, principal: OrgPrincipalDep
) -> CapabilityStatsPublic:
    return read_stats(session, principal)


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
    definitions, errors, warnings = parse_dictionary(content, file.filename or "")
    return preview_version(session, principal, definitions, errors, warnings=warnings)


@router.post("/versions", response_model=ActiveSchemaPublic)
def save_dictionary(
    body: SaveDictionary, session: SessionDep, principal: OrgAdminDep
) -> ActiveSchemaPublic:
    return publish_dictionary(session, principal, body)


@router.get("/editor", response_model=DictionaryPreview)
def read_dictionary_editor(
    session: SessionDep, principal: OrgAdminDep
) -> DictionaryPreview:
    return read_editor(session, principal)


@router.post("/editor/preview", response_model=DictionaryPreview)
def preview_dictionary_editor(
    body: SaveDictionary, session: SessionDep, principal: OrgAdminDep
) -> DictionaryPreview:
    preview = preview_version(session, principal, body.definitions, [])
    if preview.expected_version != body.expected_version:
        raise DictionaryConflict()
    return preview


@router.put("", response_model=ActiveSchemaPublic)
def update_dictionary(
    body: SaveDictionary, session: SessionDep, principal: OrgAdminDep
) -> ActiveSchemaPublic:
    return publish_dictionary(session, principal, body)


class DictionaryTooLarge(HTTPException):
    def __init__(self) -> None:
        super().__init__(413, "文件超过 2 MB，请缩小后上传")
