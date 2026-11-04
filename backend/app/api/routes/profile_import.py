from typing import Literal

from fastapi import APIRouter, UploadFile
from fastapi.responses import FileResponse

from app.api.deps import CurrentUser, SessionDep
from app.authz.deps import OrgAdminDep, OrgPrincipalDep
from app.domains.change_request.batch_import import ImportResult, import_profiles
from app.domains.change_request.import_json import (
    FILES_DIR,
    MAX_FILE_SIZE,
    import_profile,
)
from app.domains.change_request.schemas import RequestPreview

router = APIRouter(tags=["profile-import"])


@router.post("/orgs/{org_id}/profile-imports")
def upload_profile_imports(
    files: list[UploadFile],
    principal: OrgAdminDep,
    session: SessionDep,
    confirm: bool = False,
) -> list[ImportResult]:
    return import_profiles(
        principal,
        session,
        [
            (file.filename or "profile.json", file.file.read(MAX_FILE_SIZE + 1))
            for file in files
        ],
        confirm=confirm,
    )


@router.get("/profile-import/files/{name}", response_class=FileResponse)
def download_import_file(
    name: Literal["schema", "example", "skill"], _current_user: CurrentUser
) -> FileResponse:
    filename = {
        "schema": "v1.schema.json",
        "example": "example.json",
        "skill": "SKILL.md",
    }[name]
    return FileResponse(FILES_DIR / filename, filename=filename)


@router.post("/orgs/{org_id}/me/profile-import")
def upload_profile_import(
    file: UploadFile,
    principal: OrgPrincipalDep,
    session: SessionDep,
    current_user: CurrentUser,
    confirm: bool = False,
) -> RequestPreview:
    return import_profile(
        principal,
        session,
        current_user.email,
        file.file.read(MAX_FILE_SIZE + 1),
        file.filename or "profile.json",
        confirm=confirm,
    )
