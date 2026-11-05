import json
from collections import Counter
from uuid import UUID, uuid4

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

from app.domains.access_token.models import McpSetting
from app.domains.audit.models import AuditEvent
from app.domains.bot.tools import TOOLS as BOT_TOOLS
from app.domains.change_request.models import (
    ProfileChangeRequest,
    RequestOrigin,
    RequestStatus,
    ReviewDecision,
)
from app.domains.evidence.models import Evidence
from app.domains.manager_tools import MANAGER_TOOLS
from app.domains.org.models import (
    Department,
    Employee,
    Organization,
    OrgMembership,
    Role,
    RoleGrant,
)
from app.domains.org.service import list_employees
from app.domains.profile.models import ProfileVersion
from app.domains.review import service
from app.mcp.server import endpoint, lifespan
from tests.api.routes.test_mcp import call, initialize, token_for
from tests.api.routes.test_reviews import manager, pending


def test_sdk_manager_flow(demo: tuple, db: Session) -> None:
    _, request = pending(db, demo)
    reviewer = manager(db, demo)
    previous_revision = max(
        db.exec(
            select(ProfileVersion.revision).where(
                ProfileVersion.org_id == reviewer.org_id,
                ProfileVersion.subject_employee_id == demo[1]["E02"].id,
            )
        ).all()
    )
    headers, _ = token_for(db, demo[1]["E01"])
    before = {
        e.id: e.model_dump()
        for e in db.exec(select(Evidence).where(Evidence.org_id == reviewer.org_id))
    }
    expected = service.get_review(reviewer, db, request.id).model_dump(mode="json")
    app = FastAPI(lifespan=lifespan)
    app.router.routes.append(Route("/mcp", endpoint, methods=["POST", "GET", "DELETE"]))

    async def exercise() -> dict:
        async with (
            app.router.lifespan_context(app),
            httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app), headers=headers
            ) as http,
            streamable_http_client(
                "http://testserver/mcp", http_client=http
            ) as streams,
            ClientSession(
                *streams,
                client_info=types.Implementation(name="Manager client", version="1"),
            ) as sdk,
        ):
            await sdk.initialize()

            async def use(name: str, arguments: dict | None = None) -> dict:
                result = await sdk.call_tool(name, arguments or {})
                assert not result.is_error, result
                return result.structured_content

            listing = await use("list_pending_reviews")
            assert str(request.id) in {r["id"] for r in listing["data"]}
            review = await use("get_review", {"request_id": str(request.id)})
            assert review == expected
            decisions = [
                {
                    "item_id": item["after"]["id"],
                    "decision": "approved",
                    "reason": "合成原因",
                }
                for item in review["items"]
            ]
            row = db.get(ProfileChangeRequest, request.id)
            row.revision += 1
            db.commit()
            args = {
                "request_id": str(request.id),
                "revision": review["revision"],
                "decisions": decisions,
                "comment": "经理明确同意的合成意见",
            }
            stale = await sdk.call_tool("decide_review_items", args)
            assert stale.is_error and '"status": 409' in stale.content[0].text
            assert not db.exec(
                select(ReviewDecision).where(
                    ReviewDecision.org_id == reviewer.org_id,
                    ReviewDecision.request_id == request.id,
                )
            ).all()
            latest = await use("get_review", {"request_id": str(request.id)})
            args["revision"] = latest["revision"]
            return await use("decide_review_items", args)

    result = anyio.run(exercise)
    db.expire_all()
    assert result["status"] == "approved"
    version = db.get(ProfileVersion, UUID(result["published_version_id"]))
    assert version.org_id == reviewer.org_id
    assert version.revision == previous_revision + 1
    decision = db.get(ReviewDecision, UUID(result["decision_id"]))
    assert decision.via == "mcp" and decision.client_name == "Manager client"
    assert decision.reviewer_user_id == reviewer.user_id
    for id_, original in before.items():
        assert db.get(Evidence, id_).model_dump() == original
    audit = db.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == reviewer.org_id, AuditEvent.action == "mcp.tool_called"
        )
    ).all()
    assert {a.target_id for a in audit} == {
        "list_pending_reviews",
        "get_review",
        "decide_review_items",
    }
    assert all(a.details["client_name"] == "Manager client" for a in audit)
    assert headers["Authorization"].split()[1] not in json.dumps(
        [a.model_dump(mode="json") for a in audit]
    )


@pytest.mark.parametrize(
    "identity", ["owner", "manager_self", "admin_self", "unrelated"]
)
def test_review_visibility_and_no_self_approval(
    demo: tuple, db: Session, client: TestClient, identity: str
) -> None:
    _, request = pending(
        db,
        demo,
        role="manager.platform" if identity.endswith("_self") else "employee.platform",
    )
    person = demo[1][
        {
            "owner": "E02",
            "manager_self": "E01",
            "admin_self": "E01",
            "unrelated": "E05",
        }[identity]
    ]
    if identity == "admin_self":
        member = db.exec(
            select(OrgMembership).where(
                OrgMembership.org_id == person.org_id,
                OrgMembership.employee_id == person.id,
            )
        ).one()
        grants = db.exec(
            select(RoleGrant).where(
                RoleGrant.org_id == person.org_id, RoleGrant.role == Role.ORG_ADMIN
            )
        ).all()
        for grant in grants:
            db.delete(grant)
        db.add(
            RoleGrant(
                org_id=person.org_id, membership_id=member.id, role=Role.ORG_ADMIN
            )
        )
        db.commit()
    headers, _ = token_for(db, person)
    active = initialize(client, headers)
    listing = call(client, active, "list_pending_reviews", {})
    if identity == "admin_self":
        assert str(request.id) not in {
            r["id"] for r in listing["structuredContent"]["data"]
        }
        assert all(r["can_decide"] for r in listing["structuredContent"]["data"])
    else:
        assert listing["structuredContent"] == {"data": [], "count": 0}
    expected = 404 if identity == "unrelated" else 403
    for tool in ("get_review", "decide_review_items"):
        arguments = {"request_id": str(request.id)}
        if tool == "decide_review_items":
            arguments.update(
                revision=request.revision,
                decisions=[
                    {"item_id": i.id.hex, "decision": "approved"} for i in request.items
                ],
            )
        result = call(client, active, tool, arguments)
        assert (
            result["isError"]
            and json.loads(result["content"][0]["text"])["status"] == expected
        )
        if identity == "unrelated" and tool == "decide_review_items":
            arguments["revision"] += 1
            result = call(client, active, tool, arguments)
            assert json.loads(result["content"][0]["text"])["status"] == 404
    assert not db.exec(
        select(ReviewDecision).where(ReviewDecision.org_id == person.org_id)
    ).all()


def test_foreign_request_and_disabled_mcp(
    demo: tuple, db: Session, client: TestClient
) -> None:
    _, request = pending(db, demo)
    headers, token = token_for(db, demo[1]["E01"])
    active = initialize(client, headers)
    foreign_user = jwt.decode(
        demo[2]["foreign"]["Authorization"].split()[1],
        options={"verify_signature": False},
    )["sub"]
    foreign = db.exec(
        select(Organization)
        .join(OrgMembership)
        .where(OrgMembership.user_id == UUID(foreign_user))
    ).one()
    subject = Employee(org_id=foreign.id, display_name="合成的外部员工")
    db.add(subject)
    db.flush()
    row = ProfileChangeRequest(
        org_id=foreign.id,
        subject_employee_id=subject.id,
        origin=RequestOrigin.MANUAL,
        status=RequestStatus.PENDING_REVIEW,
        author={},
    )
    db.add(row)
    db.commit()
    for id_ in (row.id, uuid4()):
        for tool in ("get_review", "decide_review_items"):
            args = {"request_id": str(id_)}
            if tool == "decide_review_items":
                args.update(
                    revision=1,
                    decisions=[{"item_id": str(uuid4()), "decision": "approved"}],
                )
            result = call(client, active, tool, args)
            assert (
                result["isError"]
                and json.loads(result["content"][0]["text"])["status"] == 404
            )
    db.get(McpSetting, token.org_id).enabled = False
    db.commit()
    for tool in MANAGER_TOOLS:
        response = client.post(
            "/mcp",
            headers=active,
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": tool.name,
                    "arguments": {"request_id": str(request.id)},
                },
            },
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "企业已关闭外部 AI 助手"


def test_inputs_are_strict_and_bot_has_no_manager_tools(
    demo: tuple, db: Session, client: TestClient
) -> None:
    _, request = pending(db, demo)
    headers, _ = token_for(db, demo[1]["E01"])
    active = initialize(client, headers)
    valid = {
        "request_id": str(request.id),
        "revision": request.revision,
        "decisions": [
            {"item_id": str(i.id), "decision": "approved"} for i in request.items
        ],
    }
    for tool in MANAGER_TOOLS:
        assert tool.name not in BOT_TOOLS and not tool.bot_allowed
        for field in ("org_id", "employee_id", "via", "client_name", "user_id"):
            args = (
                valid
                if tool.writes
                else {"request_id": str(request.id)}
                if tool.name == "get_review"
                else {}
            )
            result = call(client, active, tool.name, {**args, field: "forged"})
            assert (
                result["isError"]
                and result["content"][0]["text"] == "工具参数无效，请核对字段和格式"
            )
    for invalid in (
        {k: v for k, v in valid.items() if k != "revision"},
        {
            **valid,
            "decisions": [
                {"item_id": str(request.items[0].id), "decision": "rejected"}
            ],
        },
        {**valid, "decisions": [{**valid["decisions"][0], "via": "web"}]},
    ):
        assert call(client, active, "decide_review_items", invalid)["isError"]
    assert not db.exec(
        select(ReviewDecision).where(ReviewDecision.org_id == demo[1]["E01"].org_id)
    ).all()


def test_team_overview_uses_employee_scope_and_all_pages(
    demo: tuple, db: Session, client: TestClient
) -> None:
    _, request = pending(db, demo)
    person = demo[1]["E01"]
    child = Department(
        org_id=person.org_id,
        name="合成子部门",
        parent_department_id=person.department_id,
    )
    db.add(child)
    db.flush()
    db.add_all(
        [
            Employee(
                org_id=person.org_id,
                department_id=child.id,
                display_name=f"合成员工{i}",
            )
            for i in range(101)
        ]
    )
    db.commit()
    principal = manager(db, demo)
    expected = list_employees(
        db, principal, department_id=None, q=None, skip=0, limit=200
    )
    assert len(expected.data) == 104
    assert demo[1]["E05"].id not in {e.id for e in expected.data}
    headers, _ = token_for(db, person)
    result = call(client, initialize(client, headers), "get_team_overview", {})
    assert not result["isError"]
    overview = result["structuredContent"]
    assert overview["employee_count"] == 104
    assert {k: v for k, v in overview["profile_status_counts"].items() if v} == dict(
        Counter(e.progress.review_status for e in expected.data)
    )
    assert (
        overview["pending_review_count"]
        == service.list_pending_reviews(principal, db).count
        == 1
    )
    assert db.get(ProfileChangeRequest, request.id).status == "pending_review"
