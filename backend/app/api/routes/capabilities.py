from uuid import UUID

from fastapi import APIRouter, HTTPException, Response, UploadFile

from app.api.deps import SessionDep
from app.authz.deps import OrgAdminDep, OrgPrincipalDep
from app.domains.capability.files import (
    dictionary_yaml,
    parse_dictionary,
)
from app.domains.capability.proposals import (
    dismiss_proposal,
    list_proposals,
    suggest_capability,
)
from app.domains.capability.publish import save_dictionary as publish_dictionary
from app.domains.capability.schemas import (
    ActiveSchemaPublic,
    CapabilitiesPublic,
    CapabilityProposalsPublic,
    CapabilityPublic,
    CapabilityStatsPublic,
    CapabilitySuggestionResult,
    DictionaryPreview,
    SaveDictionary,
    SuggestCapability,
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


@router.get("/proposals", response_model=CapabilityProposalsPublic)
def read_capability_proposals(
    session: SessionDep, principal: OrgAdminDep
) -> CapabilityProposalsPublic:
    return list_proposals(session, principal)


@router.post("/proposals", response_model=CapabilitySuggestionResult)
def suggest_new_capability(
    body: SuggestCapability, session: SessionDep, principal: OrgPrincipalDep
) -> CapabilitySuggestionResult:
    return suggest_capability(principal, session, **body.model_dump())


@router.post("/proposals/{id}/dismiss", status_code=204)
def dismiss_capability_proposal(
    id: UUID, session: SessionDep, principal: OrgAdminDep
) -> None:
    dismiss_proposal(session, principal, id)
