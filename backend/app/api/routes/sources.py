from hashlib import sha256
from typing import Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel

from app.api.deps import SessionDep
from app.api.secret_safe import SecretSafeRoute
from app.authz.deps import OrgAdminDep
from app.domains.source.schemas import (
    JiraConnectionPublic,
    JiraConnectionTest,
    JiraConnectionUpdate,
    SyncRunPublic,
    SyncRunsPublic,
)
from app.domains.source.service import (
    InvalidSourceSetting,
    check_jira,
    public_jira,
    read_jira,
    read_sync_runs,
    save_jira,
)
from app.domains.source.sync import SyncError, create_run, retry_run
from app.domains.work.records import (
    WorkRecordIssue,
    build_work_records_template_csv,
    build_work_records_template_xlsx,
    parse_work_records,
)

router = APIRouter(
    prefix="/orgs/{org_id}/sources",
    tags=["sources"],
    route_class=SecretSafeRoute,
)
sync_router = APIRouter(prefix="/orgs/{org_id}/sync-runs", tags=["sources"])


def _sync_error(error: SyncError) -> HTTPException:
    return HTTPException(error.status, error.message)


@router.post("/jira/sync", status_code=202, response_model=SyncRunPublic)
def start_jira_sync(session: SessionDep, principal: OrgAdminDep) -> SyncRunPublic:
    try:
        return create_run(session, principal, "jira")
    except SyncError as error:
        raise _sync_error(error) from None


@router.get("/csv/template", response_class=Response)
def work_records_template(
    _principal: OrgAdminDep, format: Literal["xlsx", "csv"] = "xlsx"
) -> Response:
    return Response(
        build_work_records_template_xlsx()
        if format == "xlsx"
        else build_work_records_template_csv(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if format == "xlsx"
        else "text/csv",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(f'工作记录模板.{format}', safe='')}"
        },
    )


class CsvImportPublic(BaseModel):
    run: SyncRunPublic
    issues: list[WorkRecordIssue]


@router.post("/csv/imports", status_code=202, response_model=CsvImportPublic)
def import_work_records(
    file: UploadFile, session: SessionDep, principal: OrgAdminDep
) -> CsvImportPublic:
    content = file.file.read(5 * 1024 * 1024 + 1)
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(413, "文件超过 5 MB，请拆分后再上传")
    filename = file.filename or ""
    parsed = parse_work_records(content, filename)
    if parsed.has_errors:
        raise HTTPException(
            422,
            {
                "code": "work_records_invalid",
                "issues": [i.model_dump() for i in parsed.issues],
            },
        )
    try:
        run = create_run(
            session,
            principal,
            "csv",
            {
                "filename": filename,
                "file_sha256": sha256(content).hexdigest(),
                "rows": [r.model_dump(mode="json") for r in parsed.rows],
            },
        )
    except SyncError as error:
        raise _sync_error(error) from None
    return CsvImportPublic(run=run, issues=parsed.issues)


@sync_router.get("", response_model=SyncRunsPublic)
def list_sync_runs(
    session: SessionDep,
    principal: OrgAdminDep,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    kind: Literal["jira", "csv"] | None = None,
) -> SyncRunsPublic:
    return read_sync_runs(session, principal, skip, limit, kind)


@sync_router.post("/{run_id}/retry", status_code=202, response_model=SyncRunPublic)
def retry_sync(
    run_id: UUID, session: SessionDep, principal: OrgAdminDep
) -> SyncRunPublic:
    try:
        return retry_run(session, principal, run_id)
    except SyncError as error:
        raise _sync_error(error) from None


@router.get(
    "/jira", response_model=JiraConnectionPublic, response_model_exclude_unset=True
)
def get_jira(session: SessionDep, principal: OrgAdminDep) -> JiraConnectionPublic:
    return public_jira(read_jira(session, principal.org_id))


@router.put("/jira", response_model=JiraConnectionPublic)
def put_jira(
    body: JiraConnectionUpdate,
    session: SessionDep,
    principal: OrgAdminDep,
) -> JiraConnectionPublic:
    try:
        return save_jira(session, principal, body)
    except InvalidSourceSetting as error:
        raise HTTPException(422, str(error)) from None


@router.post("/jira/test", response_model=JiraConnectionTest)
def test_jira(session: SessionDep, principal: OrgAdminDep) -> JiraConnectionTest:
    return check_jira(session, principal)
