from fastapi import APIRouter

from app.api.deps import SessionDep
from app.domains.setup.schemas import InitializeInput, InitializeResult, SetupStatus
from app.domains.setup.service import initialize, is_initialized

router = APIRouter(prefix="/setup", tags=["setup"])


@router.get("/status")
def read_status(session: SessionDep) -> SetupStatus:
    return SetupStatus(initialized=is_initialized(session))


@router.post("/initialize")
def initialize_organization(
    data: InitializeInput, session: SessionDep
) -> InitializeResult:
    result = initialize(session, data)
    session.commit()
    return result
