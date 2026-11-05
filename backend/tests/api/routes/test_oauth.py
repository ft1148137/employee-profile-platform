import base64
import json
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import Engine, inspect
from sqlmodel import Session, col, delete, func, select
from starlette.routing import Route

from app.core.config import settings
from app.core.security import ALGORITHM
from app.domains.access_token.models import (
    McpSetting,
    OAuthClient,
    OAuthGrant,
    OAuthRefreshToken,
    PersonalAccessToken,
)
from app.domains.access_token.oauth import digest
from app.domains.audit.models import AuditEvent
from app.domains.base import utc_now
from app.domains.change_request.models import (
    ProfileChangeRequest,
    RequestOrigin,
    RequestStatus,
    ReviewDecision,
)
from app.domains.org.models import Employee, OrgMembership
from app.mcp.oauth import oauth_routes, resource_url
from app.mcp.server import endpoint, lifespan
from app.models import User
from tests.api.routes.test_mcp import call, initialize
from tests.api.routes.test_reviews import pending
from tests.utils.migrations import migrate

VERIFIER = "a" * 43
CHALLENGE = (
    base64.urlsafe_b64encode(bytes.fromhex(digest(VERIFIER))).decode().rstrip("=")
)
CALLBACK = "https://client.example/callback"


@pytest.fixture
def registered(demo: tuple, db: Session, client: TestClient) -> Generator[dict]:
    url, _, auth = demo
    before = set(db.exec(select(OAuthClient.client_id)).all())
    assert (
        client.put(
            url + "/mcp-setting", headers=auth["admin"], json={"enabled": True}
        ).status_code
        == 200
    )
    info = client.post(
        "/oauth/register",
        json={"client_name": "Synthetic client", "redirect_uris": [CALLBACK]},
    ).json()
    assert "client_id" in info, info
    info["consent_org_id"] = url.rsplit("/", 1)[-1]
    try:
        yield info
    finally:
        db.rollback()
        clients = set(db.exec(select(OAuthClient.client_id)).all()) - before
        for grant in db.exec(
            select(OAuthGrant).where(col(OAuthGrant.client_id).in_(clients))
        ).all():
            db.exec(
                delete(PersonalAccessToken).where(
                    PersonalAccessToken.org_id == grant.org_id,
                    PersonalAccessToken.oauth_grant_id == grant.id,
                )
            )
            db.exec(
                delete(OAuthRefreshToken).where(
                    OAuthRefreshToken.org_id == grant.org_id,
                    OAuthRefreshToken.grant_id == grant.id,
                )
            )
            db.delete(grant)
        db.flush()
        db.exec(delete(OAuthClient).where(col(OAuthClient.client_id).in_(clients)))
        db.commit()


def request_for(client: TestClient, info: dict, **overrides: str) -> str:
    response = client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": info["client_id"],
            "redirect_uri": CALLBACK,
            "code_challenge": CHALLENGE,
            "code_challenge_method": "S256",
            "state": "synthetic-state",
            "resource": resource_url(),
            **overrides,
        },
        follow_redirects=False,
    )
    assert response.status_code == 302, response.text
    return parse_qs(urlsplit(response.headers["location"]).query)["request"][0]


def approve(client: TestClient, info: dict, auth: dict[str, str]) -> str:
    response = client.post(
        "/api/v1/oauth/consent?org_id=" + info["consent_org_id"],
        headers=auth,
        json={"request": request_for(client, info), "approve": True},
    )
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    query = parse_qs(urlsplit(response.json()["redirect_to"]).query)
    assert query["state"] == ["synthetic-state"]
    return query["code"][0]


def token(client: TestClient, info: dict, code: str, **extra: str) -> Response:
    return client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": info["client_id"],
            "client_secret": info.get("client_secret", ""),
            "code": code,
            "redirect_uri": CALLBACK,
            "code_verifier": VERIFIER,
            **extra,
        },
    )


def refresh(client: TestClient, info: dict, renewal: str, **extra: str) -> Response:
    return client.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": info["client_id"],
            "client_secret": info.get("client_secret", ""),
            "refresh_token": renewal,
            **extra,
        },
    )


def headers(access: str) -> dict[str, str]:
    return {
        "Authorization": "Bearer " + access,
        "Accept": "application/json, text/event-stream",
    }


def test_protocol_lifecycle_and_secrets(
    registered: dict,
    demo: tuple,
    db: Session,
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _, people, auth = demo
    challenge = client.post("/mcp", json={})
    assert (
        challenge.status_code == 401
        and 'resource_metadata="' in challenge.headers["www-authenticate"]
    )
    protected = client.get("/.well-known/oauth-protected-resource").json()
    assert protected["resource"] == resource_url()
    assert client.get("/.well-known/oauth-protected-resource/mcp").json() == protected
    metadata = client.get("/.well-known/oauth-authorization-server").json()
    assert metadata["code_challenge_methods_supported"] == ["S256"]
    assert set(metadata["grant_types_supported"]) == {
        "authorization_code",
        "refresh_token",
    }
    assert metadata["authorization_endpoint"].endswith("/oauth/authorize")
    assert metadata["registration_endpoint"].endswith("/oauth/register")
    assert "none" in metadata["token_endpoint_auth_methods_supported"]
    assert (
        client.get("/.well-known/oauth-authorization-server/oauth").json() == metadata
    )
    code = approve(client, registered, auth["employee"])
    result = token(client, registered, code, resource=resource_url())
    assert result.status_code == 200, result.text
    pair = result.json()
    assert pair["expires_in"] == 3600 and pair["scope"] == "employee"
    active = initialize(client, headers(pair["access_token"]), name="Synthetic client")
    assert call(client, active, "get_my_profile", {})["structuredContent"]["employee"][
        "id"
    ] == str(people["E02"].id)
    updated = refresh(client, registered, pair["refresh_token"])
    assert updated.status_code == 200, updated.text
    new = updated.json()
    assert (
        new["access_token"] != pair["access_token"]
        and new["refresh_token"] != pair["refresh_token"]
    )
    assert (
        client.post(
            "/mcp", headers={**active, **headers(new["access_token"])}, json={}
        ).status_code
        == 404
    )
    active = initialize(client, headers(new["access_token"]), name="Synthetic client")
    assert not call(client, active, "get_my_profile", {})["isError"]
    grant = db.exec(
        select(OAuthGrant).where(OAuthGrant.code_hash == digest(code))
    ).one()
    access_rows = db.exec(
        select(PersonalAccessToken).where(
            PersonalAccessToken.org_id == grant.org_id,
            PersonalAccessToken.oauth_grant_id == grant.id,
        )
    ).all()
    renewals = db.exec(
        select(OAuthRefreshToken).where(
            OAuthRefreshToken.org_id == grant.org_id,
            OAuthRefreshToken.grant_id == grant.id,
        )
    ).all()
    assert all(
        r.client_kind == "oauth" and r.employee_id == people["E02"].id
        for r in access_rows
    )
    assert all(
        timedelta(days=29) < r.expires_at - utc_now() <= timedelta(days=30)
        for r in renewals
    )
    stored = db.get(OAuthClient, registered["client_id"])
    assert stored.client_secret_encrypted != registered["client_secret"]
    assert "client_secret" not in stored.client_metadata
    public = json.dumps(
        [r.model_dump(mode="json") for r in [grant, *access_rows, *renewals, stored]]
    )
    for secret in (
        code,
        pair["access_token"],
        pair["refresh_token"],
        new["access_token"],
        new["refresh_token"],
        registered["client_secret"],
    ):
        assert secret not in public
    revoked = client.post(
        "/oauth/revoke",
        data={
            "client_id": registered["client_id"],
            "client_secret": registered["client_secret"],
            "token": new["refresh_token"],
        },
    )
    assert revoked.status_code == 200, revoked.text
    for access in (pair["access_token"], new["access_token"]):
        assert client.post("/mcp", headers=headers(access), json={}).status_code == 401
    assert refresh(client, registered, new["refresh_token"]).status_code == 400
    audit = db.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == grant.org_id,
            col(AuditEvent.action).startswith("oauth."),
        )
    ).all()
    assert {r.action for r in audit} == {"oauth.allowed", "oauth.revoked"}
    audit_text = json.dumps([r.model_dump(mode="json") for r in audit]) + caplog.text
    # HTTP test-client URL logging includes consent JWTs; credentials never enter server errors/audit.
    assert all(
        secret not in audit_text
        for secret in (code, pair["refresh_token"], registered["client_secret"])
    )


@pytest.mark.parametrize(
    "failure", ["pkce", "redirect", "resource", "expired", "client_secret"]
)
def test_code_exchange_failures(
    registered: dict, demo: tuple, db: Session, client: TestClient, failure: str
) -> None:
    code = approve(client, registered, demo[2]["employee"])
    options = {
        "pkce": {"code_verifier": "b" * 43},
        "redirect": {"redirect_uri": "https://other.example/callback"},
        "resource": {"resource": "https://other.example/mcp"},
        "client_secret": {"client_secret": "bad"},
    }
    if failure == "expired":
        grant = db.exec(
            select(OAuthGrant).where(OAuthGrant.code_hash == digest(code))
        ).one()
        grant.code_expires_at = utc_now() - timedelta(seconds=1)
        db.commit()
    response = token(client, registered, code, **options.get(failure, {}))
    assert response.status_code in (400, 401)
    assert response.json()["error"] == (
        "invalid_target"
        if failure == "resource"
        else "invalid_client"
        if failure == "client_secret"
        else "invalid_request"
        if failure == "redirect"
        else "invalid_grant"
    )
    assert not db.exec(
        select(PersonalAccessToken).where(PersonalAccessToken.client_kind == "oauth")
    ).all()


@pytest.mark.parametrize("reuse", ["code", "refresh"])
def test_replay_revokes_chain(
    registered: dict, demo: tuple, db: Session, client: TestClient, reuse: str
) -> None:
    code = approve(client, registered, demo[2]["employee"])
    first = token(client, registered, code).json()
    second = refresh(client, registered, first["refresh_token"]).json()
    replay = (
        token(client, registered, code)
        if reuse == "code"
        else refresh(client, registered, first["refresh_token"])
    )
    assert replay.status_code == 400 and replay.json()["error"] == "invalid_grant"
    assert refresh(client, registered, second["refresh_token"]).status_code == 400
    for access in (first["access_token"], second["access_token"]):
        assert client.post("/mcp", headers=headers(access), json={}).status_code == 401
    db.expire_all()
    grant = db.exec(
        select(OAuthGrant).where(OAuthGrant.code_hash == digest(code))
    ).one()
    assert grant.revoked_at
    assert all(
        r.revoked_at
        for r in db.exec(
            select(PersonalAccessToken).where(
                PersonalAccessToken.org_id == grant.org_id,
                PersonalAccessToken.oauth_grant_id == grant.id,
            )
        ).all()
    )


@pytest.mark.parametrize("stage", ["consent", "exchange", "refresh", "mcp"])
@pytest.mark.parametrize(
    "invalid", ["disabled", "allowlist", "inactive", "password", "membership"]
)
def test_rechecks_identity_and_policy(
    registered: dict,
    demo: tuple,
    db: Session,
    client: TestClient,
    invalid: str,
    stage: str,
) -> None:
    _, people, auth = demo
    request = request_for(client, registered)
    code = approve(client, registered, auth["employee"]) if stage != "consent" else None
    pair = (
        token(client, registered, code).json() if stage in ("refresh", "mcp") else None
    )
    member = db.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == people["E02"].org_id,
            OrgMembership.employee_id == people["E02"].id,
        )
    ).one()
    user = db.get(User, member.user_id)
    original = user.is_active, user.must_change_password, member.employee_id
    setting = db.get(McpSetting, member.org_id)
    if invalid == "disabled":
        setting.enabled = False
    elif invalid == "allowlist":
        setting.allowed_clients = ["Another client"]
    elif invalid == "inactive":
        user.is_active = False
    elif invalid == "password":
        user.must_change_password = True
    else:
        member.employee_id = None
    db.commit()
    try:
        if stage == "consent":
            response = client.post(
                "/api/v1/oauth/consent?org_id=" + registered["consent_org_id"],
                headers=auth["employee"],
                json={"request": request, "approve": True},
            )
        elif stage == "exchange":
            response = token(client, registered, code)
        elif stage == "refresh":
            response = refresh(client, registered, pair["refresh_token"])
        else:
            response = client.post(
                "/mcp", headers=headers(pair["access_token"]), json={}
            )
        assert response.status_code in (400, 401, 403)
        if stage in ("exchange", "refresh"):
            assert response.json()["error"] == "invalid_grant"
    finally:
        user.is_active, user.must_change_password, member.employee_id = original
        db.commit()


def test_authorize_and_consent_boundaries(
    registered: dict, demo: tuple, db: Session, client: TestClient
) -> None:
    _, _, auth = demo
    for field, value in [
        ("resource", "https://other.example/mcp"),
        ("redirect_uri", "https://other.example/callback"),
        ("code_challenge_method", "plain"),
        ("response_type", "token"),
        ("scope", "admin"),
        ("code_challenge", "bad"),
    ]:
        response = client.get(
            "/oauth/authorize",
            params={
                "client_id": registered["client_id"],
                "redirect_uri": CALLBACK,
                "resource": resource_url(),
                "response_type": "code",
                "code_challenge": CHALLENGE,
                "code_challenge_method": "S256",
                field: value,
            },
            follow_redirects=False,
        )
        assert response.status_code in (302, 400)
        if response.status_code == 302:
            errors = parse_qs(urlsplit(response.headers["location"]).query)
            assert "error" in errors and "request" not in errors
            if field == "resource":
                assert errors["error"] == ["invalid_target"]
    request = request_for(client, registered)
    endpoint = "/api/v1/oauth/consent?org_id=" + registered["consent_org_id"]
    body = {"request": request, "approve": True}
    assert client.post(endpoint, json=body).status_code == 401
    for role in ("admin", "foreign"):
        assert client.post(endpoint, headers=auth[role], json=body).status_code == (
            404 if role == "foreign" else 403
        )
    assert (
        client.post(
            endpoint, headers=auth["employee"], json={**body, "org_id": "forged"}
        ).status_code
        == 422
    )
    denied = client.post(
        endpoint, headers=auth["employee"], json={**body, "approve": False}
    )
    assert parse_qs(urlsplit(denied.json()["redirect_to"]).query)["error"] == [
        "access_denied"
    ]
    assert not db.exec(select(OAuthGrant)).all()
    for bad in (
        request + "tampered",
        jwt.encode(
            {"aud": "oauth-consent", "exp": utc_now() - timedelta(minutes=1)},
            settings.SECRET_KEY,
            algorithm=ALGORITHM,
        ),
    ):
        assert (
            client.post(
                endpoint, headers=auth["employee"], json={**body, "request": bad}
            ).status_code
            == 400
        )
    pair = token(
        client, registered, approve(client, registered, auth["employee"])
    ).json()
    active = initialize(client, headers(pair["access_token"]), name="Synthetic client")
    assert call(client, active, "get_my_profile", {"org_id": str(UUID(int=0))})[
        "isError"
    ]
    assert call(client, active, "get_my_profile", {"employee_id": str(UUID(int=0))})[
        "isError"
    ]
    # An OAuth access token cannot authenticate as a browser user or select another tenant.
    assert (
        client.get(
            demo[0] + "/employees", headers=headers(pair["access_token"])
        ).status_code
        == 401
    )


def test_registration_limits_and_public_client(
    registered: dict,
    demo: tuple,
    db: Session,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert registered["client_id"]
    metadata = {
        "client_name": "Public client",
        "redirect_uris": [CALLBACK],
        "token_endpoint_auth_method": "none",
    }
    info = client.post("/oauth/register", json=metadata).json()
    info["consent_org_id"] = registered["consent_org_id"]
    assert info.get("client_secret") is None
    code = approve(client, info, demo[2]["employee"])
    pair = token(client, info, code).json()
    assert "access_token" in pair
    # The integration supplies SDK 2.3.0's missing optional client_secret default.
    assert (
        client.post(
            "/oauth/revoke",
            data={
                "client_id": info["client_id"],
                "token": pair["access_token"],
            },
        ).status_code
        == 200
    )
    assert (
        client.post("/mcp", headers=headers(pair["access_token"]), json={}).status_code
        == 401
    )
    for overrides in (
        {"redirect_uris": ["javascript:alert(1)"]},
        {"client_name": ""},
        {"client_name": "A" + " " * 100},
        {"client_name": "A" * 101},
        {"scope": "admin"},
        {"grant_types": ["authorization_code", "password"]},
    ):
        assert (
            client.post("/oauth/register", json={**metadata, **overrides}).status_code
            == 400
        )
    monkeypatch.setattr(settings, "OAUTH_REGISTER_MAX_PER_IP_HOUR", 2)
    response = client.post("/oauth/register", json=metadata)
    assert response.status_code == 429 and response.headers["retry-after"] == "3600"
    assert db.exec(select(func.count()).select_from(OAuthClient)).one() == 2


def test_concurrent_code_exchange(
    registered: dict, demo: tuple, client: TestClient
) -> None:
    code = approve(client, registered, demo[2]["employee"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: token(client, registered, code), range(2)))
    assert sorted(r.status_code for r in results) == [200, 400]
    issued = next(r.json() for r in results if r.status_code == 200)
    assert (
        client.post(
            "/mcp", headers=headers(issued["access_token"]), json={}
        ).status_code
        == 401
    )


def test_oauth_migration(migration_engine: Engine) -> None:
    migrate(migration_engine, "downgrade", "a95e001bc001")
    assert not {"oauth_client", "oauth_grant", "oauth_refresh_token"} & set(
        inspect(migration_engine).get_table_names()
    )
    migrate(migration_engine, "upgrade", "head")
    inspector = inspect(migration_engine)
    assert {"oauth_client", "oauth_grant", "oauth_refresh_token"} <= set(
        inspector.get_table_names()
    )
    assert any(
        fk["constrained_columns"] == ["org_id", "oauth_grant_id"]
        for fk in inspector.get_foreign_keys("personal_access_token")
    )
    for table in ("oauth_grant", "oauth_refresh_token"):
        assert "token" not in {c["name"] for c in inspector.get_columns(table)}


@pytest.mark.parametrize("kind", ["code", "refresh"])
def test_expired_reuse_still_revokes(
    registered: dict, demo: tuple, db: Session, client: TestClient, kind: str
) -> None:
    code = approve(client, registered, demo[2]["employee"])
    first = token(client, registered, code).json()
    active = refresh(client, registered, first["refresh_token"]).json()
    if kind == "code":
        row = db.exec(
            select(OAuthGrant).where(OAuthGrant.code_hash == digest(code))
        ).one()
        row.code_expires_at = utc_now() - timedelta(seconds=1)
    else:
        row = db.exec(
            select(OAuthRefreshToken).where(
                OAuthRefreshToken.token_hash == digest(first["refresh_token"])
            )
        ).one()
        row.expires_at = utc_now() - timedelta(seconds=1)
    db.commit()
    response = (
        token(client, registered, code)
        if kind == "code"
        else refresh(client, registered, first["refresh_token"], scope="admin")
    )
    assert response.status_code == 400
    assert (
        client.post(
            "/mcp", headers=headers(active["access_token"]), json={}
        ).status_code
        == 401
    )


def test_tenant_objects_and_client_ownership(
    registered: dict, demo: tuple, db: Session, client: TestClient
) -> None:
    _, people, auth = demo
    foreign_user = UUID(
        jwt.decode(
            auth["foreign"]["Authorization"].split()[1],
            options={"verify_signature": False},
        )["sub"]
    )
    membership = db.exec(
        select(OrgMembership).where(OrgMembership.user_id == foreign_user)
    ).one()
    foreign_employee = Employee(
        org_id=membership.org_id, display_name="Synthetic foreign employee"
    )
    db.add(foreign_employee)
    db.flush()
    foreign_request = ProfileChangeRequest(
        org_id=membership.org_id,
        subject_employee_id=foreign_employee.id,
        origin=RequestOrigin.MANUAL,
        status=RequestStatus.DRAFT,
        author={},
    )
    db.add(foreign_request)
    db.commit()
    code = approve(client, registered, auth["employee"])
    other = client.post(
        "/oauth/register",
        json={"client_name": "Other client", "redirect_uris": [CALLBACK]},
    ).json()
    assert token(client, other, code).json()["error"] == "invalid_grant"
    pair = token(client, registered, code).json()
    active = initialize(client, headers(pair["access_token"]), name="Synthetic client")
    result = call(
        client, active, "preview_request", {"request_id": str(foreign_request.id)}
    )
    assert result["isError"] and '"status": 404' in result["content"][0]["text"]
    assert (
        client.post(
            "/oauth/revoke",
            data={
                "client_id": other["client_id"],
                "client_secret": other["client_secret"],
                "token": pair["access_token"],
            },
        ).status_code
        == 200
    )
    assert not call(client, active, "get_my_profile", {})["isError"]
    for field in ("org_id", "user_id", "employee_id"):
        response = client.post(
            "/api/v1/oauth/consent?org_id=" + registered["consent_org_id"],
            headers=auth["employee"],
            json={
                "request": request_for(client, registered),
                "approve": True,
                field: str(people["E03"].id),
            },
        )
        assert response.status_code == 422


def test_refresh_expiry_cleanup_and_concurrency(
    registered: dict, demo: tuple, db: Session, client: TestClient
) -> None:
    first = token(
        client, registered, approve(client, registered, demo[2]["employee"])
    ).json()
    old = db.exec(
        select(PersonalAccessToken).where(
            PersonalAccessToken.token_hash == digest(first["access_token"])
        )
    ).one()
    assert timedelta(minutes=59) < old.expires_at - utc_now() <= timedelta(hours=1)
    old.expires_at = utc_now() - timedelta(seconds=1)
    old_id = old.id
    db.commit()
    second = refresh(client, registered, first["refresh_token"]).json()
    db.expire_all()
    assert db.get(PersonalAccessToken, old_id) is None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: refresh(client, registered, second["refresh_token"]), range(2)
            )
        )
    assert sorted(r.status_code for r in results) == [200, 400]
    issued = next(r.json() for r in results if r.status_code == 200)
    assert (
        client.post(
            "/mcp", headers=headers(issued["access_token"]), json={}
        ).status_code
        == 401
    )


def test_employee_revoke_also_stops_refresh(
    registered: dict, demo: tuple, db: Session, client: TestClient
) -> None:
    pair = token(
        client, registered, approve(client, registered, demo[2]["employee"])
    ).json()
    access = db.exec(
        select(PersonalAccessToken).where(
            PersonalAccessToken.token_hash == digest(pair["access_token"])
        )
    ).one()
    endpoint = demo[0] + "/me/access-tokens/" + str(access.id)
    assert client.delete(endpoint, headers=demo[2]["foreign"]).status_code == 404
    response = client.delete(endpoint, headers=demo[2]["employee"])
    assert response.status_code == 200 and response.json()["revoked_at"]
    assert (
        refresh(client, registered, pair["refresh_token"]).json()["error"]
        == "invalid_grant"
    )
    assert (
        client.post("/mcp", headers=headers(pair["access_token"]), json={}).status_code
        == 401
    )


def test_basic_client_auth_without_form_client_id(
    registered: dict, demo: tuple, client: TestClient
) -> None:
    info = client.post(
        "/oauth/register",
        json={
            "client_name": "Basic client",
            "redirect_uris": [CALLBACK],
            "token_endpoint_auth_method": "client_secret_basic",
        },
    ).json()
    info["consent_org_id"] = registered["consent_org_id"]
    code = approve(client, info, demo[2]["employee"])
    authorization = {
        "Authorization": "Basic "
        + base64.b64encode(
            (info["client_id"] + ":" + info["client_secret"]).encode()
        ).decode()
    }
    response = client.post(
        "/oauth/token",
        headers=authorization,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": CALLBACK,
            "code_verifier": VERIFIER,
        },
    )
    assert response.status_code == 200, response.text
    pair = response.json()
    invalid = client.post(
        "/oauth/token",
        headers=authorization,
        data={
            "grant_type": "refresh_token",
            "refresh_token": pair["refresh_token"],
            "resource": "https://foreign.example/mcp",
        },
    )
    assert invalid.status_code == 400 and invalid.json()["error"] == "invalid_target"
    response = client.post(
        "/oauth/revoke", headers=authorization, data={"token": pair["refresh_token"]}
    )
    assert response.status_code == 200, response.text
    assert (
        client.post("/mcp", headers=headers(pair["access_token"]), json={}).status_code
        == 401
    )


def test_mcp_uses_authorized_client_name(
    registered: dict, demo: tuple, client: TestClient
) -> None:
    assert (
        client.put(
            demo[0] + "/mcp-setting",
            headers=demo[2]["admin"],
            json={"enabled": True, "allowed_clients": [registered["client_name"]]},
        ).status_code
        == 200
    )
    pair = token(
        client, registered, approve(client, registered, demo[2]["employee"])
    ).json()
    # OAuth registration names can differ from the transport library's clientInfo.
    active = initialize(
        client, headers(pair["access_token"]), name="Different transport library"
    )
    assert not call(client, active, "get_my_profile", {})["isError"]


def test_mcp_oauth_manager_decision(
    demo: tuple, db: Session, client: TestClient, registered: dict
) -> None:
    _, request = pending(db, demo)
    code = approve(client, registered, demo[2]["manager"])
    authorized = token(client, registered, code)
    assert authorized.status_code == 200, authorized.text
    active = initialize(
        client, headers(authorized.json()["access_token"]), name="Untrusted name"
    )
    review = call(client, active, "get_review", {"request_id": str(request.id)})
    assert not review["isError"]
    detail = review["structuredContent"]
    result = call(
        client,
        active,
        "decide_review_items",
        {
            "request_id": str(request.id),
            "revision": detail["revision"],
            "decisions": [
                {"item_id": i["after"]["id"], "decision": "approved"}
                for i in detail["items"]
            ],
        },
    )
    assert not result["isError"]
    assert result["structuredContent"]["published_version_id"]
    decision = db.get(ReviewDecision, UUID(result["structuredContent"]["decision_id"]))
    assert decision.via == "mcp"
    assert decision.client_name == "Synthetic client"


@pytest.mark.parametrize(
    "origin, status",
    [("http://192.0.2.10:8080", 503), ("https://profiles.example", 200)],
)
def test_http_installation_does_not_crash_oauth_startup(
    monkeypatch: pytest.MonkeyPatch, origin: str, status: int
) -> None:
    monkeypatch.setattr(settings, "FRONTEND_HOST", origin)
    app = FastAPI()
    app.router.routes.extend(oauth_routes())
    with TestClient(app) as local:
        response = local.get("/.well-known/oauth-authorization-server")
        assert response.status_code == status
        if status == 503:
            assert "HTTPS" in response.json()["error_description"]
            for path in (
                "/oauth/token",
                "/oauth/register",
                "/oauth/authorize",
                "/oauth/revoke",
            ):
                denied = local.post(path, data={"client_secret": "synthetic-secret"})
                assert (
                    denied.status_code == 503 and "synthetic-secret" not in denied.text
                )
        else:
            assert response.json()["issuer"] == origin + "/oauth"


def test_resource_matches_canonical_metadata(
    monkeypatch: pytest.MonkeyPatch, registered: dict, demo: tuple, client: TestClient
) -> None:
    monkeypatch.setattr(settings, "FRONTEND_HOST", "https://Profiles.Example:443/")
    app = FastAPI(lifespan=lifespan)
    app.router.routes.extend(oauth_routes())
    app.router.routes.append(Route("/mcp", endpoint, methods=["POST", "GET", "DELETE"]))
    pair = token(
        client, registered, approve(client, registered, demo[2]["employee"])
    ).json()
    with TestClient(app, base_url="https://profiles.example") as local:
        metadata = local.get("/.well-known/oauth-protected-resource").json()
        assert metadata["resource"] == resource_url() == "https://profiles.example/mcp"
        active = initialize(
            local,
            {**headers(pair["access_token"]), "Origin": "https://profiles.example"},
        )
        assert not call(local, active, "get_my_profile", {})["isError"]


@pytest.mark.parametrize(
    ("policy", "reason"),
    [
        (None, None),
        ("disabled", "企业已关闭"),
        ("allowlist", "允许列表"),
        ("admin", "没有员工身份"),
    ],
)
def test_consent_preview(
    registered: dict,
    demo: tuple,
    client: TestClient,
    policy: str | None,
    reason: str | None,
) -> None:
    url, _, auth = demo
    if policy in ("disabled", "allowlist"):
        client.put(
            url + "/mcp-setting",
            headers=auth["admin"],
            json={
                "enabled": policy != "disabled",
                "allowed_clients": ["Other client"] if policy == "allowlist" else [],
            },
        )
    endpoint = "/api/v1/oauth/consent"
    request = request_for(client, registered)
    params = {"org_id": registered["consent_org_id"], "request": request}
    result = client.get(
        endpoint,
        params=params,
        headers=auth["admin" if policy == "admin" else "employee"],
    )
    assert result.status_code == 200, result.text
    assert result.headers["cache-control"] == "no-store"
    data = result.json()
    assert (
        data["client_name"] == "Synthetic client"
        and data["callback_host"] == "client.example"
    )
    assert data["org_name"]
    assert reason in data["reason"] if reason else data["reason"] is None
    assert "client_secret" not in result.text and request not in result.text
    assert client.get(endpoint, params=params).status_code == 401
    assert (
        client.get(endpoint, params=params, headers=auth["foreign"]).status_code == 404
    )
    assert (
        client.get(
            endpoint,
            params={**params, "request": request + "tampered"},
            headers=auth["employee"],
        ).status_code
        == 400
    )


def test_grants_owner_tenant_revocation(
    registered: dict, demo: tuple, db: Session, client: TestClient
) -> None:
    url, _, auth = demo
    pair = token(
        client, registered, approve(client, registered, auth["employee"])
    ).json()
    endpoint = "/api/v1/oauth/grants"
    params = {"org_id": registered["consent_org_id"]}
    result = client.get(endpoint, params=params, headers=auth["employee"])
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
    grant = result.json()[0]
    assert set(grant) == {"id", "client_name", "created_at", "last_used_at"}
    assert grant["client_name"] == "Synthetic client" and grant["last_used_at"] is None
    assert client.get(endpoint, params=params, headers=auth["admin"]).json() == []
    assert client.get(endpoint, params=params, headers=auth["manager"]).json() == []
    assert (
        client.get(endpoint, params=params, headers=auth["foreign"]).status_code == 404
    )
    active = initialize(client, headers(pair["access_token"]), name="Synthetic client")
    assert not call(client, active, "get_my_profile", {})["isError"]
    assert client.get(endpoint, params=params, headers=auth["employee"]).json()[0][
        "last_used_at"
    ]
    used_at = client.get(endpoint, params=params, headers=auth["employee"]).json()[0][
        "last_used_at"
    ]
    row = db.exec(
        select(PersonalAccessToken).where(
            PersonalAccessToken.token_hash == digest(pair["access_token"])
        )
    ).one()
    row.expires_at = utc_now() - timedelta(seconds=1)
    db.add(row)
    db.commit()
    rotated = refresh(client, registered, pair["refresh_token"])
    assert rotated.status_code == 200
    pair = rotated.json()
    active = headers(pair["access_token"])
    assert (
        client.get(endpoint, params=params, headers=auth["employee"]).json()[0][
            "last_used_at"
        ]
        == used_at
    )
    assert client.get(url + "/me/access-tokens", headers=auth["employee"]).json() == []
    for role in ("admin", "manager", "foreign"):
        assert (
            client.delete(
                endpoint + "/" + grant["id"], params=params, headers=auth[role]
            ).status_code
            == 404
        )
    # Same user cannot use a different tenant to revoke this grant.
    grant_row = db.get(OAuthGrant, UUID(grant["id"]))
    assert grant_row is not None
    foreign_org = db.exec(
        select(OrgMembership.org_id).where(
            OrgMembership.user_id == grant_row.user_id,
            OrgMembership.org_id != grant_row.org_id,
        )
    ).first()
    assert foreign_org is not None
    assert (
        client.delete(
            endpoint + "/" + grant["id"],
            params={"org_id": str(foreign_org)},
            headers=auth["employee"],
        ).status_code
        == 404
    )
    assert (
        client.delete(
            endpoint + "/" + grant["id"], params=params, headers=auth["employee"]
        ).status_code
        == 204
    )
    assert client.get(endpoint, params=params, headers=auth["employee"]).json() == []
    assert client.post("/mcp", headers=active, json={}).status_code == 401
    assert refresh(client, registered, pair["refresh_token"]).status_code == 400
    assert (
        client.delete(
            endpoint + "/" + grant["id"], params=params, headers=auth["employee"]
        ).status_code
        == 204
    )
    assert (
        len(
            db.exec(
                select(AuditEvent).where(
                    AuditEvent.org_id == UUID(params["org_id"]),
                    AuditEvent.action == "oauth.revoked",
                )
            ).all()
        )
        == 1
    )


@pytest.mark.parametrize(
    "path,method",
    [
        ("/.well-known/oauth-protected-resource/mcp", "GET"),
        ("/.well-known/oauth-authorization-server/oauth", "GET"),
        ("/oauth/register", "POST"),
        ("/oauth/token", "POST"),
        ("/oauth/revoke", "POST"),
    ],
)
def test_browser_oauth_cors(client: TestClient, path: str, method: str) -> None:
    response = client.options(
        path,
        headers={
            "Origin": "https://inspector.example",
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in response.headers
    private = client.options(
        "/api/v1/oauth/consent",
        headers={
            "Origin": "https://inspector.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert private.status_code == 400
    assert "access-control-allow-origin" not in private.headers
