from pathlib import Path

import sentry_sdk
from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from app.api.main import api_router
from app.core.config import settings
from app.mcp.oauth import oauth_routes
from app.mcp.server import endpoint, lifespan

FRONTEND_DIR = Path(__file__).parent / "frontend"


def custom_generate_unique_id(route: APIRoute) -> str:
    return f"{route.tags[0]}-{route.name}"


if settings.SENTRY_DSN and settings.FASTAPI_ENV != "development":
    sentry_sdk.init(dsn=str(settings.SENTRY_DSN), enable_tracing=True)

app = FastAPI(
    lifespan=lifespan,
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    generate_unique_id_function=custom_generate_unique_id,
)


class ApplicationCors(CORSMiddleware):
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] == "http" and (
            path in {"/oauth/register", "/oauth/token", "/oauth/revoke"}
            or path.startswith("/.well-known/oauth-")
        ):
            # Public OAuth endpoints already carry the SDK's client-facing CORS policy.
            await self.app(scope, receive, send)
        else:
            await super().__call__(scope, receive, send)


app.add_middleware(
    ApplicationCors,
    allow_origins=[settings.FRONTEND_HOST],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "X-Issued-Count"],
)

app.include_router(api_router, prefix=settings.API_V1_STR)
app.router.routes.append(
    Route("/mcp", endpoint=endpoint, methods=["POST", "GET", "DELETE"])
)
app.router.routes.extend(oauth_routes())
if FRONTEND_DIR.is_dir():
    app.frontend("/", directory=FRONTEND_DIR)

# CI timing probe; closed without merging.
