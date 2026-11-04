import json
from datetime import timedelta
from uuid import UUID

import anyio
import httpx2
import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client
from sqlmodel import Session, select
from starlette.routing import Route

from app.authz.deps import get_org_principal
from app.core.config import settings
from app.domains.access_token.models import McpSetting, PersonalAccessToken
from app.domains.access_token.service import create_token
from app.domains.audit.models import AuditEvent
from app.domains.base import utc_now
from app.domains.change_request import employee
from app.domains.change_request.models import (
    ProfileChangeRequest,
    RequestOrigin,
    RequestStatus,
)
from app.domains.employee_tools import EMPLOYEE_TOOLS
from app.domains.evidence.models import Evidence
from app.domains.inbox.models import InboxItem
from app.domains.org.models import Employee, Organization, OrgMembership
from app.mcp.server import endpoint, lifespan
from app.models import User


def token_for(
    db: Session, employee: Employee
) -> tuple[dict[str, str], PersonalAccessToken]:
    membership = db.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == employee.org_id,
            OrgMembership.employee_id == employee.id,
        )
    ).one()
    user = db.get(User, membership.user_id)
    principal = get_org_principal(employee.org_id, user, db)
    if db.get(McpSetting, employee.org_id) is None:
        db.add(McpSetting(org_id=employee.org_id, updated_by=user.id, enabled=True))
        db.flush()
    created = create_token(db, principal, "Synthetic client")
    db.commit()
    return {
        "Authorization": f"Bearer {created.token}",
        "Accept": "application/json, text/event-stream",
    }, db.get(PersonalAccessToken, created.id)


def initialize(
    client: TestClient, headers: dict[str, str], name: str = "Cursor"
) -> dict[str, str]:
    response = client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": name, "version": "1"},
            },
        },
    )
    assert response.status_code == 200, response.text
    assert "result" in response.json(), response.text
    return {
        **headers,
        "Mcp-Session-Id": response.headers["mcp-session-id"],
        "MCP-Protocol-Version": "2025-11-25",
    }


def call(
    client: TestClient, headers: dict[str, str], name: str, arguments: dict
) -> dict:
    response = client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["result"]


def test_sdk_employee_flow(demo: tuple, db: Session) -> None:
    _, people, _ = demo
    headers, row = token_for(db, people["E02"])
    app = FastAPI(lifespan=lifespan)
    app.router.routes.append(Route("/mcp", endpoint, methods=["POST", "GET", "DELETE"]))

    async def exercise() -> str:
        async with (
            app.router.lifespan_context(app),
            httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app), headers=headers
            ) as http,
        ):
            async with (
                streamable_http_client(
                    "http://testserver/mcp", http_client=http
                ) as streams,
                ClientSession(
                    *streams,
                    client_info=types.Implementation(name="Cursor", version="1"),
                ) as sdk,
            ):
                await sdk.initialize()
                listing = await sdk.list_tools()
                assert {t.name for t in listing.tools} == {
                    t.name for t in EMPLOYEE_TOOLS
                }
                assert all(
                    not {"employee_id", "org_id", "via", "client_name"}
                    & t.input_schema["properties"].keys()
                    for t in listing.tools
                )

                async def use(name: str, args: dict | None = None) -> dict:
                    result = await sdk.call_tool(name, args or {})
                    assert not result.is_error, result
                    return result.structured_content

                profile = await use("get_my_profile")
                assert profile["employee"]["id"] == str(people["E02"].id)
                assert (await use("list_my_evidence"))["data"]
                assert (await use("get_my_requests"))["data"]
                assert (await use("get_capability_dictionary"))["data"]
                question = (await use("get_pending_questions"))["data"][0]
                answered = await use(
                    "answer_question",
                    {"question_id": question["id"], "answer": "本人确认的合成经历"},
                )
                assert answered["status"] == "answered"
                draft = await use(
                    "draft_update_request",
                    {
                        "items": [
                            {
                                "op": "add",
                                "capability_key": "automated_testing",
                                "statement": "合成能力",
                                "owner_statement": "本人确认的合成依据",
                            }
                        ]
                    },
                )
                preview = await use("preview_request", {"request_id": draft["id"]})
                submitted = await use(
                    "submit_request",
                    {"request_id": draft["id"], "revision": preview["revision"]},
                )
                assert submitted["status"] == "pending_review"
                assert (
                    submitted["submitted_via"] == "mcp"
                    and submitted["client_name"] == "Cursor"
                )
                assert any(
                    n.request_id == UUID(draft["id"]) and "Cursor" in n.message
                    for n in db.exec(
                        select(InboxItem).where(InboxItem.org_id == row.org_id)
                    ).all()
                )
                withdrawn = await use("withdraw_request", {"request_id": draft["id"]})
                assert withdrawn["status"] == "withdrawn"
                assert await use("get_my_profile") == profile
                return draft["id"]

    request_id = anyio.run(exercise)
    db.expire_all()
    assert row.last_used_at
    assert db.get(ProfileChangeRequest, UUID(request_id)).status == "withdrawn"
    assert (
        db.exec(
            select(Evidence).where(
                Evidence.org_id == row.org_id, Evidence.source == "self_report:mcp"
            )
        )
        .one()
        .visibility
        == "restricted"
    )


@pytest.mark.parametrize(
    "invalid", ["revoked", "expired", "disabled", "inactive", "password", "membership"]
)
def test_every_request_rechecks_token(
    demo: tuple,
    db: Session,
    client: TestClient,
    invalid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, people, _ = demo
    headers, row = token_for(db, people["E02"])
    active = initialize(client, headers)
    user = db.get(User, row.user_id)
    original = user.is_active, user.password_changed_at
    if invalid == "revoked":
        row.revoked_at = utc_now()
    elif invalid == "expired":
        row.expires_at = utc_now() - timedelta(seconds=1)
    elif invalid == "disabled":
        db.get(McpSetting, row.org_id).enabled = False
    elif invalid == "inactive":
        db.get(User, row.user_id).is_active = False
    elif invalid == "password":
        db.get(User, row.user_id).password_changed_at = utc_now()
    else:
        db.exec(
            select(OrgMembership).where(
                OrgMembership.org_id == row.org_id, OrgMembership.user_id == row.user_id
            )
        ).one().employee_id = None
    db.commit()
    monkeypatch.setattr(settings, "LOGIN_MAX_FAILURES_PER_IP", 6)
    try:
        for auth in (headers, active):
            for method in ("POST", "GET", "DELETE"):
                response = client.request(
                    method,
                    "/mcp",
                    headers=auth,
                    json={"jsonrpc": "2.0", "id": 9, "method": "tools/list"},
                )
                assert response.status_code == (403 if invalid == "disabled" else 401)
                if invalid == "disabled":
                    assert response.json()["detail"] == "企业已关闭外部 AI 助手"
        if invalid != "disabled":
            assert client.post("/mcp", headers=active, json={}).status_code == 429
    finally:
        user.is_active, user.password_changed_at = original
        db.commit()


def test_session_binding_and_allowed_clients(
    demo: tuple, db: Session, client: TestClient
) -> None:
    _, people, _ = demo
    headers, row = token_for(db, people["E02"])
    active = initialize(client, headers)
    other, _ = token_for(db, people["E02"])
    worker = FastAPI(lifespan=lifespan)
    worker.router.routes.append(
        Route("/mcp", endpoint, methods=["POST", "GET", "DELETE"])
    )
    with TestClient(worker) as second_worker:
        assert not call(second_worker, active, "get_my_profile", {})["isError"]
    assert (
        client.post(
            "/mcp", headers={**active, "Authorization": other["Authorization"]}, json={}
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/mcp",
            headers={**active, "Mcp-Session-Id": active["Mcp-Session-Id"] + "tampered"},
            json={},
        ).status_code
        == 404
    )
    db.get(McpSetting, row.org_id).allowed_clients = ["Cursor"]
    db.commit()
    rejected = client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "Unknown", "version": "1"},
            },
        },
    )
    assert rejected.status_code == 403
    assert not call(client, active, "get_my_profile", {})["isError"]


def test_boundaries_and_safe_errors(
    demo: tuple,
    db: Session,
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, people, auth = demo
    headers, row = token_for(db, people["E02"])
    active = initialize(client, headers)
    for args in (
        {"employee_id": str(people["E03"].id)},
        {"org_id": str(row.org_id)},
        {"client_name": "forged"},
        {"via": "web"},
    ):
        assert call(client, active, "get_my_profile", args)["isError"]
    foreign_user = jwt.decode(
        auth["foreign"]["Authorization"].split()[1], options={"verify_signature": False}
    )["sub"]
    foreign_org = db.exec(
        select(Organization)
        .join(OrgMembership)
        .where(OrgMembership.user_id == UUID(foreign_user))
    ).one()
    foreign_employee = Employee(
        org_id=foreign_org.id, display_name="Synthetic foreign employee"
    )
    db.add(foreign_employee)
    db.flush()
    other_requests = [
        ProfileChangeRequest(
            org_id=e.org_id,
            subject_employee_id=e.id,
            origin=RequestOrigin.MANUAL,
            status=RequestStatus.DRAFT,
            author={},
        )
        for e in (people["E03"], foreign_employee)
    ]
    db.add_all(other_requests)
    db.commit()
    for other in other_requests:
        for name, args in (
            ("preview_request", {}),
            ("draft_update_request", {"items": []}),
            ("submit_request", {"revision": 1}),
            ("withdraw_request", {}),
        ):
            result = call(client, active, name, {"request_id": str(other.id), **args})
            assert result["isError"] and '"status": 404' in result["content"][0]["text"]
    secret = headers["Authorization"].split()[1]
    result = call(client, active, "draft_update_request", {"items": secret})
    assert result["isError"] and secret not in json.dumps(result)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError(f"SQL synthetic failure {secret}")

    monkeypatch.setattr(employee, "read_profile", fail)
    result = call(client, active, "get_my_profile", {})
    assert result["isError"] and "操作失败" in result["content"][0]["text"]
    events = db.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == row.org_id, AuditEvent.action == "mcp.tool_called"
        )
    ).all()
    assert events and all(
        e.details == {"request_id": "2", "client_name": "Cursor"} for e in events
    )
    assert secret not in json.dumps(
        [e.model_dump(mode="json") for e in events]
    ) + caplog.text + json.dumps(result)
    assert call(client, active, "unknown", {})["isError"]


def test_invalid_tokens_rate_limit_and_origin(
    demo: tuple, db: Session, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, people, _ = demo
    headers, _ = token_for(db, people["E02"])
    active = initialize(client, headers)
    assert (
        client.post(
            "/mcp", headers={**active, "Origin": "https://evil.example"}, json={}
        ).status_code
        == 403
    )
    assert client.post("/mcp", headers=headers, json=[]).status_code == 400
    assert client.post("/mcp", headers=headers, content="{").status_code == 400
    monkeypatch.setattr(settings, "LOGIN_MAX_FAILURES_PER_IP", 3)
    for auth in (
        {},
        {"Authorization": "Basic invalid"},
        {"Authorization": "Bearer invalid"},
    ):
        response = client.post("/mcp", headers=auth, json={})
        assert (
            response.status_code == 401
            and response.headers["www-authenticate"] == "Bearer"
        )
    response = client.post("/mcp", json={})
    assert response.status_code == 429 and response.headers["Retry-After"]
