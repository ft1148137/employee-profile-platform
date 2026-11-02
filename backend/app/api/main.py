from fastapi import APIRouter

from app.api.routes import (
    capabilities,
    employees,
    evidence,
    login,
    me,
    orgs,
    private,
    profiles,
    users,
    utils,
)
from app.core.config import settings

api_router = APIRouter()
api_router.include_router(login.router)
api_router.include_router(users.router)
api_router.include_router(utils.router)
api_router.include_router(me.router)
api_router.include_router(orgs.router)
api_router.include_router(employees.router)
api_router.include_router(capabilities.router)
api_router.include_router(profiles.router)
api_router.include_router(evidence.router)


if settings.FASTAPI_ENV == "development":
    api_router.include_router(private.router)
