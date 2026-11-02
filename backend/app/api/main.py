from fastapi import APIRouter

from app.api.routes import employees, login, me, orgs, private, users, utils
from app.core.config import settings

api_router = APIRouter()
api_router.include_router(login.router)
api_router.include_router(users.router)
api_router.include_router(utils.router)
api_router.include_router(me.router)
api_router.include_router(orgs.router)
api_router.include_router(employees.router)


if settings.FASTAPI_ENV == "development":
    api_router.include_router(private.router)
