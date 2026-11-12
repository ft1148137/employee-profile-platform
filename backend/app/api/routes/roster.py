from uuid import UUID

from fastapi import APIRouter, Query, UploadFile

from app.api.deps import SessionDep
from app.authz.deps import OrgAdminDep
from app.domains.org.roster import service
from app.domains.org.roster.schemas import (
    ApplyImport,
    RosterImportPreview,
    RosterImportResult,
    RosterImportsPublic,
)
from app.domains.org.roster.single import SingleChange, preview

router = APIRouter(prefix="/orgs/{org_id}/roster", tags=["roster"])


@router.post("/single-change")
def preview_single_change(
    body: SingleChange, session: SessionDep, principal: OrgAdminDep
) -> RosterImportPreview:
    return preview(session, principal, body)


@router.post("/imports", response_model=RosterImportPreview)
def upload_import(
    file: UploadFile, session: SessionDep, principal: OrgAdminDep
) -> RosterImportPreview:
    content = file.file.read(service.MAX_FILE_SIZE + 1)
    if len(content) > service.MAX_FILE_SIZE:
        raise service.RosterError(413, "文件超过 5 MB，请拆分后再上传")
    return service.create_import(session, principal, content, file.filename or "")


@router.post("/imports/{id}/apply", response_model=RosterImportResult)
def apply_import(
    id: UUID, body: ApplyImport, session: SessionDep, principal: OrgAdminDep
) -> RosterImportResult:
    return service.apply_import(session, principal, id, body)


@router.get("/imports", response_model=RosterImportsPublic)
def read_imports(
    session: SessionDep,
    principal: OrgAdminDep,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
) -> RosterImportsPublic:
    return service.list_imports(session, principal, skip, limit)
