import base64
import re
from collections.abc import Callable
from contextvars import ContextVar
from datetime import timedelta
from typing import Concatenate
from urllib.parse import unquote, urlencode

import jwt
from mcp.server.auth.handlers.metadata import MetadataHandler
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    RegistrationError,
)
from mcp.server.auth.routes import (
    build_metadata,
    cors_middleware,
    create_auth_routes,
    create_protected_resource_routes,
)
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.server.transport_security import RequestBodyLimitMiddleware
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyHttpUrl, AnyUrl
from sqlmodel import Session, col, func, select
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import settings
from app.core.db import engine
from app.core.secrets import SecretUnavailable, decrypt_secret, encrypt_secret
from app.core.security import ALGORITHM
from app.domains.access_token.models import (
    OAuthClient,
    OAuthGrant,
    OAuthRefreshToken,
    PersonalAccessToken,
)
from app.domains.access_token.oauth import digest, exchange, revoke_by_id
from app.domains.base import utc_now

registration_ip: ContextVar[str] = ContextVar("oauth_registration_ip")


def resource_url() -> str:
    return settings.FRONTEND_HOST.rstrip("/") + "/mcp"


def database_call[T, **P](
    fn: Callable[Concatenate[Session, P], T], *args: P.args, **kwargs: P.kwargs
) -> T:
    with Session(engine) as session:
        result = fn(session, *args, **kwargs)
        session.commit()
        return result


def register(session: Session, client: OAuthClientInformationFull, ip: str) -> None:
    session.exec(
        select(func.pg_advisory_xact_lock(func.hashtext("oauth-register:" + ip)))
    ).all()
    count = session.exec(
        select(func.count())
        .select_from(OAuthClient)
        .where(
            OAuthClient.registered_ip == ip,
            OAuthClient.created_at > utc_now() - timedelta(hours=1),
        )
    ).one()
    if count >= settings.OAUTH_REGISTER_MAX_PER_IP_HOUR:
        raise RegistrationLimited()
    if (
        not client.client_name
        or not client.client_name.strip()
        or len(client.client_name) > 100
        or set(client.grant_types) - {"authorization_code", "refresh_token"}
        or client.response_types != ["code"]
        or client.scope != "employee"
    ):
        raise RegistrationError("invalid_client_metadata", "客户端配置无效")
    for uri in client.redirect_uris or []:
        if (
            uri.fragment
            or uri.username
            or uri.password
            or not (
                uri.scheme == "https"
                or uri.scheme == "http"
                and uri.host in {"localhost", "127.0.0.1", "[::1]"}
            )
        ):
            raise RegistrationError(
                "invalid_redirect_uri", "回调地址必须使用 HTTPS 或本机 HTTP"
            )
    if not client.redirect_uris:
        raise RegistrationError("invalid_redirect_uri")
    session.add(
        OAuthClient(
            client_id=client.client_id,
            client_metadata=client.model_dump(mode="json", exclude={"client_secret"}),
            client_secret_encrypted=encrypt_secret(client.client_secret)
            if client.client_secret
            else None,
            registered_ip=ip,
        )
    )


class RegistrationLimited(Exception):
    pass


class Provider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        def load(session: Session) -> OAuthClientInformationFull | None:
            row = session.get(OAuthClient, client_id)
            if row is None:
                return None
            try:
                secret = (
                    decrypt_secret(row.client_secret_encrypted)
                    if row.client_secret_encrypted
                    else None
                )
            except SecretUnavailable:
                return None
            return OAuthClientInformationFull(
                **row.client_metadata, client_secret=secret
            )

        return await run_in_threadpool(lambda: database_call(load))

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        await run_in_threadpool(
            lambda: database_call(register, client_info, registration_ip.get())
        )

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        if params.resource != resource_url():
            raise AuthorizeError("invalid_target")
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", params.code_challenge):
            raise AuthorizeError("invalid_request", "PKCE 参数无效")
        request = jwt.encode(
            {
                "aud": "oauth-consent",
                "exp": utc_now() + timedelta(minutes=10),
                "client_id": client.client_id,
                "redirect_uri": str(params.redirect_uri),
                "code_challenge": params.code_challenge,
                "state": params.state,
                "resource": params.resource,
            },
            settings.SECRET_KEY,
            algorithm=ALGORITHM,
        )
        return (
            settings.FRONTEND_HOST.rstrip("/")
            + "/oauth/consent?"
            + urlencode({"request": request})
        )

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        def load(session: Session) -> AuthorizationCode | None:
            grant = session.exec(
                select(OAuthGrant).where(
                    col(OAuthGrant.client_id) == client.client_id,
                    col(OAuthGrant.code_hash) == digest(authorization_code),
                )
            ).one_or_none()
            if grant is None or grant.revoked_at:
                return None
            if grant.code_used_at is not None:
                revoke_by_id(session, grant.org_id, grant.id)
                return None
            return AuthorizationCode(
                code=authorization_code,
                scopes=["employee"],
                expires_at=grant.code_expires_at.timestamp(),
                client_id=grant.client_id,
                code_challenge=grant.code_challenge,
                redirect_uri=AnyUrl(grant.redirect_uri),
                redirect_uri_provided_explicitly=True,
                resource=grant.resource,
            )

        return await run_in_threadpool(lambda: database_call(load))

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        return await run_in_threadpool(
            lambda: database_call(
                exchange, client.client_id, authorization_code.code, refresh=False
            )
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        def load(session: Session) -> RefreshToken | None:
            row = session.exec(
                select(OAuthRefreshToken)
                .join(
                    OAuthGrant,
                    (col(OAuthGrant.org_id) == col(OAuthRefreshToken.org_id))
                    & (col(OAuthGrant.id) == col(OAuthRefreshToken.grant_id)),
                )
                .where(
                    col(OAuthGrant.client_id) == client.client_id,
                    col(OAuthRefreshToken.token_hash) == digest(refresh_token),
                )
            ).one_or_none()
            if row is None:
                return None
            if row.used_at is not None:
                revoke_by_id(session, row.org_id, row.grant_id)
                return None
            return RefreshToken(
                token=refresh_token,
                client_id=client.client_id,
                scopes=["employee"],
                expires_at=int(row.expires_at.timestamp()),
            )

        return await run_in_threadpool(lambda: database_call(load))

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        return await run_in_threadpool(
            lambda: database_call(
                exchange, client.client_id, refresh_token.token, refresh=True
            )
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        def load(session: Session) -> AccessToken | None:
            grant = session.exec(
                select(OAuthGrant)
                .join(
                    PersonalAccessToken,
                    (col(OAuthGrant.org_id) == col(PersonalAccessToken.org_id))
                    & (col(OAuthGrant.id) == col(PersonalAccessToken.oauth_grant_id)),
                )
                .where(col(PersonalAccessToken.token_hash) == digest(token))
            ).one_or_none()
            return (
                AccessToken(token=token, client_id=grant.client_id, scopes=["employee"])
                if grant
                else None
            )

        return await run_in_threadpool(lambda: database_call(load))

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        def revoke(session: Session) -> None:
            if isinstance(token, RefreshToken):
                row = session.exec(
                    select(OAuthRefreshToken).where(
                        col(OAuthRefreshToken.token_hash) == digest(token.token)
                    )
                ).one()
                revoke_by_id(session, row.org_id, row.grant_id)
            else:
                access = session.exec(
                    select(PersonalAccessToken).where(
                        col(PersonalAccessToken.token_hash) == digest(token.token)
                    )
                ).one()
                assert access.oauth_grant_id is not None
                revoke_by_id(session, access.org_id, access.oauth_grant_id)

        await run_in_threadpool(lambda: database_call(revoke))


provider = Provider()


class OAuthEndpoint:
    def __init__(self, app: ASGIApp, path: str) -> None:
        self.app, self.path = app, path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        request = Request(scope, receive)
        body: bytes | None = await request.body()
        context = registration_ip.set(
            request.client.host if request.client else "unknown"
        )
        try:
            if self.path in ("/token", "/revoke") and request.method == "POST":
                form = await request.form()
                resources = form.getlist("resource")
                if (
                    self.path == "/token"
                    and resources
                    and resources != [resource_url()]
                ):
                    await JSONResponse(
                        {"error": "invalid_target"},
                        status_code=400,
                        headers={"Cache-Control": "no-store"},
                    )(scope, receive, send)
                    return

                normalized = dict(form)
                authorization = request.headers.get("authorization", "")
                if "client_id" not in form and authorization.startswith("Basic "):
                    try:
                        credentials = base64.b64decode(
                            authorization[6:], validate=True
                        ).decode()
                        normalized["client_id"] = unquote(credentials.split(":", 1)[0])
                    except ValueError, UnicodeError:
                        pass  # The SDK returns invalid_client for malformed credentials.
                if self.path == "/revoke" and "client_secret" not in form:
                    normalized["client_secret"] = ""
                if normalized != dict(form):
                    body = urlencode(normalized).encode()
                    scope = {
                        **scope,
                        "headers": [
                            (key, value)
                            for key, value in scope["headers"]
                            if key not in (b"content-type", b"content-length")
                        ]
                        + [
                            (b"content-type", b"application/x-www-form-urlencoded"),
                            (b"content-length", str(len(body)).encode()),
                        ],
                    }

            async def replay() -> Message:
                nonlocal body
                if body is not None:
                    data, body = body, None
                    return {"type": "http.request", "body": data, "more_body": False}
                return await receive()

            async def secure_send(message: Message) -> None:
                if message["type"] == "http.response.start":
                    message["headers"] = [
                        (key, value)
                        for key, value in message["headers"]
                        if key != b"cache-control"
                    ] + [(b"cache-control", b"no-store")]
                await send(message)

            await self.app(scope, replay, secure_send)
        except RegistrationLimited:
            await JSONResponse(
                {"error": "temporarily_unavailable"},
                status_code=429,
                headers={"Retry-After": "3600", "Cache-Control": "no-store"},
            )(scope, receive, send)
        finally:
            registration_ip.reset(context)


async def https_required(_request: Request) -> JSONResponse:
    return JSONResponse(
        {
            "error": "temporarily_unavailable",
            "error_description": "OAuth 授权需要 HTTPS，请配置 HTTPS 访问地址",
        },
        status_code=503,
        headers={"Cache-Control": "no-store"},
    )


def oauth_routes() -> list[Route]:
    issuer = AnyHttpUrl(settings.FRONTEND_HOST.rstrip("/") + "/oauth")
    if issuer.scheme != "https" and issuer.host not in {
        "localhost",
        "127.0.0.1",
        "[::1]",
    }:
        # HTTP installation remains usable; OAuth credentials require a secure issuer.
        return [
            Route(path, https_required, methods=["GET", "POST", "OPTIONS"])
            for path in (
                "/oauth/{path:path}",
                "/.well-known/oauth-authorization-server",
                "/.well-known/oauth-authorization-server/oauth",
                "/.well-known/oauth-protected-resource",
                "/.well-known/oauth-protected-resource/mcp",
            )
        ]
    registration = ClientRegistrationOptions(
        enabled=True, valid_scopes=["employee"], default_scopes=["employee"]
    )
    revocation = RevocationOptions(enabled=True)
    routes: list[Route] = []
    for route in create_auth_routes(
        provider,
        issuer,
        client_registration_options=registration,
        revocation_options=revocation,
    ):
        if route.path.startswith("/.well-known"):
            metadata = build_metadata(issuer, None, registration, revocation)
            metadata.token_endpoint_auth_methods_supported = [
                "none",
                "client_secret_post",
                "client_secret_basic",
            ]
            metadata.revocation_endpoint_auth_methods_supported = (
                metadata.token_endpoint_auth_methods_supported
            )
            app = cors_middleware(MetadataHandler(metadata).handle, ["GET", "OPTIONS"])
            routes.extend(
                Route(path, app, methods=route.methods)
                for path in [route.path, route.path + "/oauth"]
            )
        else:
            routes.append(
                Route(
                    "/oauth" + route.path,
                    RequestBodyLimitMiddleware(
                        OAuthEndpoint(route.app, route.path), 64 * 1024
                    ),
                    methods=route.methods,
                )
            )
    resource = create_protected_resource_routes(
        AnyHttpUrl(resource_url()), [issuer], scopes_supported=["employee"]
    )[0]
    routes.extend(
        [
            resource,
            Route(
                "/.well-known/oauth-protected-resource",
                resource.app,
                methods=resource.methods,
            ),
        ]
    )
    return routes
