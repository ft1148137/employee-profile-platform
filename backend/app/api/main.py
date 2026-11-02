from fastapi import APIRouter

from app.api.routes import (
    accounts,
    capabilities,
    capability_templates,
    employees,
    evidence,
    login,
    me,
    orgs,
    private,
    profiles,
    roles,
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
api_router.include_router(capability_templates.router)
api_router.include_router(profiles.router)
api_router.include_router(evidence.router)
api_router.include_router(accounts.router)
api_router.include_router(roles.router)


if settings.FASTAPI_ENV == "development":
    api_router.include_router(private.router)
