"""M3 acceptance: reuse boundary scenarios, then exercise one employee end to end."""

from uuid import UUID

import anyio
import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import text
from sqlmodel import Session, select
from starlette.routing import Route

from app.domains.bot import core
from app.domains.change_request.models import ProfileChangeRequest
from app.domains.evidence.models import Evidence
from app.domains.model.client import ModelRateLimited, ModelUnavailable
from app.domains.model.schemas import ModelSettingUpdate
from app.mcp.server import endpoint, lifespan
from tests.acceptance.test_m2 import evaluation
from tests.api.routes.test_dictionary_versions import (
    test_versions_preserve_records_remap_requests_and_block_stale_levels as test_dictionary_versions,  # noqa: F401
)
from tests.api.routes.test_employee_requests import payload
from tests.api.routes.test_employee_requests import (
    test_other_employee_and_tenant_requests as test_other_employee_and_tenant_requests,
)
from tests.api.routes.test_initial_profiles import (
    test_no_model_never_queues as test_model_required,  # noqa: F401
)
from tests.api.routes.test_login import (
    test_get_access_token as test_email_login,  # noqa: F401
)
from tests.api.routes.test_mcp import (
    test_boundaries_and_safe_errors as test_boundaries_and_safe_errors,
)
from tests.api.routes.test_mcp import token_for
from tests.api.routes.test_oauth import (  # noqa: F401
    approve,
    headers,
    registered,
    token,
)
from tests.api.routes.test_roster import tenant as tenant  # noqa: F401

# These existing scenarios are also collected here so this file is the M3 gate.
from tests.api.routes.test_roster import (
    test_email_errors_block_preview_and_apply as roster_email_scenario,
)
from tests.authz.test_tenant_access import tenant_data  # noqa: F401
from tests.domains.test_bot_core import enable
from tests.domains.test_bot_core import (
    test_no_model_and_http_scope as test_no_model_and_http_scope,
)
from tests.domains.test_drafting_model import drafting  # noqa: F401
from tests.domains.test_drafting_model import (
    test_injected_commands_and_invalid_reference_never_publish as test_injected_commands_and_invalid_reference_never_publish,
)
from tests.domains.test_identity_auto_link import (
    test_page_links_backfills_and_respects_unlink as test_page_links_backfills_and_respects_unlink,
)
from tests.domains.test_sync_schedule import context, jira  # noqa: F401
from tests.domains.test_sync_schedule import (
    test_concurrent_dispatch_and_missed_periods as test_concurrent_dispatch_and_missed_periods,
)
from tests.utils.bot import final_response
from tests.utils.pi import PiHarness

pytestmark = pytest.mark.xdist_group("model")


@pytest.mark.parametrize("problem", ["missing", "invalid", "duplicate", "occupied"])
def test_roster_email(tenant: object, db: Session, problem: str) -> None:
    from app.main import app

    with TestClient(app, headers=tenant.auth["admin"]) as authenticated:
        roster_email_scenario(tenant, authenticated, db, problem)


def test_one_employee_all_channels(
    demo: tuple,
    request: pytest.FixtureRequest,
    db: Session,
    client: TestClient,
    pi_service: PiHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registration = request.getfixturevalue("registered")
    base, people, auth = demo
    own = auth["employee"]
    profile = client.get(base + "/me/profile", headers=own).json()
    initial = next(
        r
        for r in client.get(base + "/me/requests", headers=own).json()["data"]
        if r["origin"] == "initial_sync"
    )
    assert initial["status"] == "pending_owner"
    submitted_ids: list[str] = []
    sdk_app = FastAPI(lifespan=lifespan)
    sdk_app.router.routes.append(
        Route("/mcp", endpoint, methods=["POST", "GET", "DELETE"])
    )

    async def mcp_flow(bearer: dict[str, str], request_id: str | None) -> None:
        async with (
            sdk_app.router.lifespan_context(sdk_app),
            httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=sdk_app), headers=bearer
            ) as http,
            streamable_http_client(
                "http://testserver/mcp", http_client=http
            ) as streams,
            ClientSession(
                *streams,
                client_info=types.Implementation(name="Synthetic client", version="1"),
            ) as sdk,
        ):
            await sdk.initialize()

            async def use(name: str, args: dict | None = None) -> dict:
                result = await sdk.call_tool(name, args or {})
                assert not result.is_error, result
                return result.structured_content

            assert (await use("get_my_profile"))["employee"]["id"] == str(
                people["E02"].id
            )
            assert any(
                r["id"] == initial["id"]
                for r in (await use("get_my_requests", {"status": "all"}))["data"]
            )
            assert (await use("list_my_evidence"))["data"]
            args = {"items": [payload(owner_statement="本人确认的 MCP 合成经历")]}
            if request_id:
                args["request_id"] = request_id
            draft = await use("draft_update_request", args)
            preview = await use("preview_request", {"request_id": draft["id"]})
            sent = await use(
                "submit_request",
                {"request_id": draft["id"], "revision": preview["revision"]},
            )
            assert sent["status"] == "pending_review" and sent["submitted_via"] == "mcp"
            submitted_ids.append(sent["id"])
            assert (await use("withdraw_request", {"request_id": sent["id"]}))[
                "status"
            ] == "withdrawn"
            mcp_profile = await use("get_my_profile")
            assert "department_capabilities_configured" not in mcp_profile
            assert all("in_department" not in item for item in mcp_profile["items"])
            assert mcp_profile["items"] == [
                {key: value for key, value in item.items() if key != "in_department"}
                for item in profile["items"]
            ]

    pat, _ = token_for(db, people["E02"])
    anyio.run(mcp_flow, pat, initial["id"])
    exchanged = token(client, registration, approve(client, registration, own))
    assert exchanged.status_code == 200, exchanged.text
    anyio.run(mcp_flow, headers(exchanged.json()["access_token"]), None)

    def withdraw(request_id: str, via: str) -> None:
        db.expire_all()
        row = db.get(ProfileChangeRequest, UUID(request_id))
        assert row.status == "pending_review" and row.submitted_via == via
        assert (
            client.post(
                f"{base}/me/requests/{request_id}/withdraw", headers=own
            ).json()["status"]
            == "withdrawn"
        )

    draft = client.post(
        base + "/me/requests",
        headers=own,
        json={"items": [payload(owner_statement="本人确认的表单合成经历")]},
    ).json()
    preview = client.get(
        f"{base}/me/requests/{draft['id']}/preview", headers=own
    ).json()
    sent = client.post(
        f"{base}/me/requests/{draft['id']}/submit",
        headers=own,
        json={"revision": preview["revision"]},
    )
    assert sent.status_code == 200, sent.text
    submitted_ids.append(draft["id"])
    withdraw(draft["id"], "web")

    enable(db, people["E02"].org_id)
    monkeypatch.setattr(core, "model_config", lambda _: pi_service.config)
    query = {"org_id": str(people["E02"].org_id)}
    bot = "/api/v1/bot/sessions"
    session_id = client.post(bot, params=query, headers=own).json()["session_id"]
    bot += f"/{session_id}"
    pi_service.replies = [
        {"calls": [{"name": "get_my_requests", "arguments": {}}]},
        {
            "calls": [
                {
                    "name": "draft_update_request",
                    "arguments": {
                        "items": [payload(owner_statement="本人确认的机器人合成经历")]
                    },
                }
            ]
        },
    ]
    turn = client.post(
        bot + "/turns",
        params=query,
        headers=own,
        json={"text": "过目初始画像，补充合成经历"},
    )
    assert turn.status_code == 200, turn.text
    card = next(
        m for m in final_response(turn)["messages"] if m["type"] == "preview_card"
    )
    request_id = card["request_id"]
    pi_service.replies = [
        {
            "calls": [
                {
                    "name": "submit_request",
                    "arguments": {
                        "request_id": request_id,
                        "revision": card["actions"][0]["revision"],
                    },
                }
            ]
        },
        "已提交",
    ]
    assert (
        client.post(
            bot + "/turns", params=query, headers=own, json={"text": "直接提交"}
        ).status_code
        == 200
    )
    db.expire_all()
    assert db.get(ProfileChangeRequest, UUID(request_id)).status == "draft"
    sent = client.post(
        bot + "/actions", params=query, headers=own, json=card["actions"][0]
    )
    assert sent.status_code == 200, sent.text
    submitted_ids.append(request_id)
    withdraw(request_id, "bot")
    assert len(set(submitted_ids)) == 4
    assert (
        client.get(base + "/me/profile", headers=own).json()["items"]
        == profile["items"]
    )
    reports = db.exec(
        select(Evidence).where(
            Evidence.org_id == people["E02"].org_id,
            Evidence.source.in_(
                [
                    "self_report:mcp",
                    "self_report:web",
                    "self_report:bot",
                ]
            ),
        )
    ).all()
    assert len(reports) == 4 and all(e.visibility == "restricted" for e in reports)


def test_baseline_quota_exit_cleans_only_its_jobs(
    monkeypatch: pytest.MonkeyPatch,
    pi_service: PiHarness,
) -> None:
    from app.core.db import engine
    from app.domains.drafting import service

    original = service.pause_drafting
    scheduled: list[UUID] = []

    def pause(org_id: UUID, ids: list[UUID], error: ModelRateLimited) -> None:
        original(org_id, ids, error)
        with Session(engine) as check:
            assert service.drafting_jobs(check, org_id, queued_only=True)
        scheduled.append(org_id)

    def exhausted(*_args: object, **_kwargs: object) -> None:
        raise ModelRateLimited(None)

    monkeypatch.setattr(service.model, "draft", exhausted)
    monkeypatch.setattr(service, "pause_drafting", pause)
    with evaluation["enterprise"]("demo-org") as (db, other, _):
        db.execute(
            text(
                "INSERT INTO procrastinate_jobs (queue_name, task_name, args) VALUES ('default', 'profiles.draft_initial', jsonb_build_object('org_id', CAST(:org AS text)))"
            ),
            {"org": str(other.id)},
        )
        db.commit()
        before = db.execute(text("SELECT id FROM procrastinate_jobs")).scalars().all()
        with pytest.raises(ModelUnavailable, match="模型起草未完成"):
            evaluation["evaluate"](
                "qiguang-aiot",
                ModelSettingUpdate(
                    enabled=True,
                    kind="openai_compatible",
                    provider="openai-compatible",
                    model="synthetic",
                    base_url=pi_service.config.baseUrl,
                ),
            )
        assert (
            db.execute(text("SELECT id FROM procrastinate_jobs")).scalars().all()
            == before
        )
        assert len(scheduled) == 1
