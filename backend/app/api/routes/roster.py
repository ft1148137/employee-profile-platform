from typing import Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Query, Response, UploadFile

from app.api.deps import SessionDep
from app.authz.deps import OrgAdminDep
from app.domains.org.roster import service
from app.domains.org.roster.schemas import (
    ApplyImport,
    RosterImportPreview,
    RosterImportResult,
    RosterImportsPublic,
)
from app.domains.org.roster.template import build_template_csv, build_template_xlsx

router = APIRouter(prefix="/orgs/{org_id}/roster", tags=["roster"])


@router.get("/template", response_class=Response)
def download_template(
    _principal: OrgAdminDep, format: Literal["xlsx", "csv"] = "xlsx"
) -> Response:
    content = build_template_xlsx() if format == "xlsx" else build_template_csv()
    media_type = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if format == "xlsx"
        else "text/csv"
    )
    return Response(
        content,
        media_type=media_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(f'员工名单模板.{format}', safe='')}",
        },
    )


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
