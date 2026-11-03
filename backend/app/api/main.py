from fastapi import APIRouter

from app.api.routes import (
    accounts,
    audit,
    capabilities,
    capability_templates,
    change_requests,
    employees,
    evidence,
    external_identities,
    initial_profiles,
    login,
    me,
    model_setting,
    orgs,
    private,
    profiles,
    roles,
    roster,
    setup,
    setup_steps,
    sources,
    users,
    utils,
)
from app.core.config import settings

api_router = APIRouter()
api_router.include_router(login.router)
api_router.include_router(users.router)
api_router.include_router(utils.router)
api_router.include_router(me.router)
api_router.include_router(model_setting.router)
api_router.include_router(sources.router)
api_router.include_router(external_identities.router)
api_router.include_router(orgs.router)
api_router.include_router(setup_steps.router)
api_router.include_router(employees.router)
api_router.include_router(capabilities.router)
api_router.include_router(capability_templates.router)
api_router.include_router(profiles.router)
api_router.include_router(change_requests.router)
api_router.include_router(evidence.router)
api_router.include_router(initial_profiles.router)
api_router.include_router(accounts.router)
api_router.include_router(audit.router)
api_router.include_router(roles.router)
api_router.include_router(roster.router)
api_router.include_router(setup.router)


if settings.FASTAPI_ENV == "development":
    api_router.include_router(private.router)
