from typing import Literal

from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.api.deps import CurrentUser
from app.domains.change_request.import_json import FILES_DIR

router = APIRouter(tags=["profile-import"])


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
