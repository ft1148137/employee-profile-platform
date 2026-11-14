import asyncio
import json
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.authz.principal import Principal
from app.core.db import engine
from app.domains.base import utc_now
from app.domains.bot import core, store, tools
from app.domains.capability.search import PeopleSearchInput
from app.domains.evidence.models import Evidence
from app.domains.im import binding, gateway
from app.domains.im.adapters.feishu import Feishu
from app.domains.im.models import ImSetting
from app.domains.manager_tools import MANAGER_TOOLS
from app.domains.model.pi_client import TurnResult
from app.domains.org.models import Department, OrgMembership, Role, RoleGrant
from app.domains.profile.models import ProfileVersion
from tests.api.routes.test_mcp import call, initialize, token_for
from tests.domains.test_bot_core import enable
from tests.domains.test_im_gateway import principal
from tests.utils.bot import final_response
from tests.utils.pi import PiHarness


@pytest.fixture
def people(demo: tuple, db: Session, client: TestClient) -> tuple:
    url, employees, auth = demo
    org_id = employees["E01"].org_id
    dictionary = client.get(f"{url}/capabilities", headers=auth["manager"]).json()
    capability = next(c for c in dictionary["data"] if c["key"] == "backend_api")
    p = principal(db, org_id, employees["E01"].id)
    child = Department(
        org_id=org_id,
        name="合成下级",
        parent_department_id=employees["E01"].department_id,
    )
    upper = Department(org_id=org_id, name="合成上级")
    foreign_user_id = jwt.decode(
        auth["foreign"]["Authorization"].split()[1],
        options={"verify_signature": False},
    )["sub"]
    foreign = db.exec(
        select(OrgMembership).where(OrgMembership.user_id == UUID(foreign_user_id))
    ).one()
    foreign_department = Department(org_id=foreign.org_id, name="合成外企业")
    db.add_all([child, upper, foreign_department])
    db.flush()
    department = db.get_one(Department, employees["E01"].department_id)
    department.parent_department_id = upper.id
    employees["E03"].department_id = child.id
    employees["E04"].department_id = upper.id
    employees["E05"].department_id = child.id
    employees["E05"].employment_status = "inactive"
    db.add_all([department, *employees.values()])
    now = utc_now().replace(microsecond=0)
    for code, subject in employees.items():
        evidence = Evidence(
            org_id=org_id,
            subject_employee_id=subject.id,
            kind="self_report",
            source="synthetic",
            source_ref=str(uuid4()),
            title="SECRET_TITLE",
            content={"text": "SECRET_RESTRICTED_BODY"},
            provenance={},
            content_hash=str(uuid4()),
            visibility="restricted",
            occurred_at=now - timedelta(days=30),
        )
        db.add(evidence)
        db.add(
            ProfileVersion(
                org_id=org_id,
                subject_employee_id=subject.id,
                revision=100,
                schema_version_id=dictionary["schema_version"]["id"],
                snapshot={
                    "claims": [
                        {
                            "id": str(uuid4()),
                            "capability_definition_id": capability["id"],
                            "statement": "合成已发布陈述",
                            "level": None if code == "E02" else "independent_complex",
                            "assessment_status": "tentative"
                            if code == "E02"
                            else "confirmed",
                            "explanation": "SECRET_EXPLANATION",
                            "confidence": None,
                            "generated_by": {"actor_type": "user"},
                            "evidence": [
                                {
                                    "id": str(evidence.id),
                                    "kind": "self_report",
                                    "title": "SECRET_TITLE",
                                    "relation": "supports",
                                }
                            ],
                        }
                    ],
                },
                published_by={"actor_type": "user", "user_id": str(p.user_id)},
                published_at=now,
            )
        )
    db.commit()
    return demo, capability, now, foreign_department


def assert_public(result: dict, expected: dict, *, links: bool) -> None:
    assert result["count"] == expected["count"]
    assert len(result["data"]) == len(expected["data"])
    for actual, row in zip(result["data"], expected["data"], strict=True):
        assert actual == (
            {**row, "profile_url": f"/employees/{row['employee_id']}"} if links else row
        )
        assert set(row) == {
            "employee_id",
            "display_name",
            "employee_code",
            "department_name",
            "capability_key",
            "capability_name",
            "statement",
            "level",
            "level_name",
            "status",
            "evidence_count",
            "latest_evidence_at",
            "published_by",
            "published_at",
        }
        assert set(row["published_by"]) == {"actor_type", "display_name"}
    assert "SECRET" not in json.dumps(result)
    assert all(
        key not in json.dumps(result)
        for key in ('"explanation"', '"content"', '"title"', '"evidence"')
    )


def grant_admin(db: Session, p: Principal) -> None:
    member = db.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == p.org_id, OrgMembership.user_id == p.user_id
        )
    ).one()
    db.add(RoleGrant(org_id=p.org_id, membership_id=member.id, role=Role.ORG_ADMIN))
    db.commit()


def model_search(
    monkeypatch: pytest.MonkeyPatch,
    args: dict,
    results: list[dict],
    *,
    allowed: bool = True,
) -> None:
    def respond(*_args: object, **kwargs: Any) -> TurnResult:
        assert ("find_people" in {t.name for t in kwargs["tools"]}) == allowed
        assert "未知不等于不会" in kwargs["system_prompt"]
        results.append(kwargs["on_tool_call"]("find_people", args))
        return TurnResult(
            text="请查看匹配员工的画像",
            stop_reason="stop",
            model_calls=1,
            tool_calls=1,
        )

    monkeypatch.setattr(core.pi_client, "run_turn", respond)


@pytest.mark.parametrize("dual_admin", [False, True])
def test_web_manager_scope_and_privacy(
    people: tuple,
    db: Session,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    dual_admin: bool,
) -> None:
    demo, capability, now, foreign_department = people
    url, employees, auth = demo
    p = principal(db, employees["E01"].org_id, employees["E01"].id)
    if dual_admin:
        grant_admin(db, p)
    enable(db, p.org_id)
    session_id = client.post(
        "/api/v1/bot/sessions",
        params={"org_id": str(p.org_id)},
        headers=auth["manager"],
    ).json()["session_id"]
    for filters, codes in (
        ({}, {"E01", "E02", "E03"}),
        (
            {
                "minimum_level": "independent_complex",
                "evidence_from": (now - timedelta(days=365)).date().isoformat(),
            },
            {"E01", "E03"},
        ),
        (
            {"minimum_level": "independent_complex", "include_ungraded": True},
            {"E01", "E02", "E03"},
        ),
        ({"statuses": ["tentative"]}, {"E02"}),
        ({"evidence_from": (now + timedelta(days=1)).date().isoformat()}, set()),
        ({"department_id": str(employees["E03"].department_id)}, {"E03"}),
    ):
        args = {"capability_key": capability["key"], **filters}
        results: list[dict] = []
        model_search(monkeypatch, args, results)
        final_response(
            client.post(
                f"/api/v1/bot/sessions/{session_id}/turns",
                params={"org_id": str(p.org_id)},
                headers=auth["manager"],
                json={"text": "找有相关能力的人"},
            )
        )
        # The assistant restricts even a dual-role administrator to managed scope.
        expected = client.get(
            f"{url}/people-search",
            params=args,
            headers=auth["manager"],
        ).json()
        if dual_admin and "department_id" not in args:
            expected["data"] = [
                r
                for r in expected["data"]
                if r["employee_code"] in {"E01", "E02", "E03"}
            ]
            expected["count"] = len(expected["data"])
        assert_public(results[0], expected, links=True)
        assert {r["employee_code"] for r in results[0]["data"]} == codes
    p = principal(db, p.org_id, p.employee_id)
    for department_id in (employees["E04"].department_id, foreign_department.id):
        with pytest.raises(HTTPException) as error:
            tools.call(
                db,
                p,
                store.get_session(db, p, UUID(session_id)),
                [],
                "find_people",
                {
                    "capability_key": capability["key"],
                    "department_id": str(department_id),
                },
            )
        assert error.value.status_code == 403


def test_web_employee_and_admin_without_departments_denied(
    people: tuple,
    db: Session,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    demo, capability, _, _ = people
    _, employees, auth = demo
    p = principal(db, employees["E02"].org_id, employees["E02"].id)
    enable(db, p.org_id)
    results: list[dict] = []
    model_search(
        monkeypatch, {"capability_key": capability["key"]}, results, allowed=False
    )
    session = store.create_session(db, p)
    final_response(
        client.post(
            f"/api/v1/bot/sessions/{session.id}/turns",
            params={"org_id": str(p.org_id)},
            headers=auth["employee"],
            json={"text": "强行找人"},
        )
    )
    assert results == [{"error": "不允许调用此工具"}]
    grant_admin(db, p)
    p = principal(db, p.org_id, p.employee_id)
    assert "find_people" not in {t.name for t in tools.definitions(p)}
    assert tools.call(db, p, session, [], "find_people", {}) == results[0]


@pytest.mark.parametrize("role", ["employee", "manager", "admin", "dual_admin"])
def test_mcp_scope_and_privacy(
    people: tuple,
    db: Session,
    client: TestClient,
    role: str,
) -> None:
    demo, capability, now, foreign_department = people
    url, employees, auth = demo
    subject = employees["E02" if role in ("employee", "admin") else "E01"]
    if role in ("admin", "dual_admin"):
        grant_admin(db, principal(db, subject.org_id, subject.id))
    headers, _ = token_for(db, subject)
    active = initialize(client, headers)
    args = {
        "capability_key": capability["key"],
        "minimum_level": "independent_complex",
        "evidence_from": (now - timedelta(days=365)).date().isoformat(),
    }
    result = call(client, active, "find_people", args)
    if role == "employee":
        assert result["isError"]
        assert json.loads(result["content"][0]["text"])["status"] == 403
        return
    assert not result.get("isError")
    expected = client.get(
        f"{url}/people-search",
        params=args,
        headers=auth["employee" if role == "admin" else "manager"],
    ).json()
    assert_public(result["structuredContent"], expected, links=False)
    assert {r["employee_code"] for r in result["structuredContent"]["data"]} == (
        set(employees) - {"E02", "E05"}
        if role in ("admin", "dual_admin")
        else {"E01", "E03"}
    )
    assert json.loads(result["content"][0]["text"]) == expected
    if role == "manager":
        denied = call(
            client,
            active,
            "find_people",
            {
                **args,
                "department_id": str(foreign_department.id),
            },
        )
        assert denied["isError"]
        assert json.loads(denied["content"][0]["text"])["status"] == 403
    tampered = call(
        client,
        active,
        "find_people",
        {
            **args,
            "org_id": str(foreign_department.org_id),
        },
    )
    assert tampered["isError"]


def test_mcp_departed_manager_old_token(
    people: tuple,
    db: Session,
    client: TestClient,
) -> None:
    demo, capability, _, _ = people
    subject = demo[1]["E01"]
    headers, _ = token_for(db, subject)
    active = initialize(client, headers)
    subject.employment_status = "inactive"
    db.add(subject)
    db.commit()
    response = client.post(
        "/mcp",
        headers=active,
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "find_people",
                "arguments": {"capability_key": capability["key"]},
            },
        },
    )
    assert response.status_code == 404
    assert "SECRET" not in response.text


@pytest.mark.parametrize("invalid", [None, "unbound", "inactive"])
def test_feishu_adapter_gateway_scope_and_old_binding(
    people: tuple,
    db: Session,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    invalid: str | None,
) -> None:
    demo, capability, now, _ = people
    url, employees, auth = demo
    p = principal(db, employees["E01"].org_id, employees["E01"].id)
    enable(db, p.org_id)
    setting = ImSetting(
        org_id=p.org_id,
        platform="feishu",
        enabled=True,
        credentials="synthetic-encrypted",
        bot_identity="synthetic-app",
        updated_by_user_id=p.user_id,
        email_auto_bind=False,
    )
    db.add(setting)
    db.commit()
    assert binding.bind(
        db,
        p.org_id,
        "feishu",
        "synthetic-user",
        "synthetic-chat",
        "synthetic-app",
        binding.create_code(db, p).code,
    )
    args = {
        "capability_key": capability["key"],
        "minimum_level": "independent_complex",
        "evidence_from": (now - timedelta(days=365)).date().isoformat(),
    }
    expected = client.get(
        f"{url}/people-search",
        params=args,
        headers=auth["manager"],
    ).json()
    results: list[dict] = []
    model_search(monkeypatch, args, results)
    outgoing: list[dict] = []

    async def exercise() -> None:
        adapter = Feishu(
            p.org_id, {"app_id": "synthetic-app", "app_secret": "synthetic"}
        )

        async def request(_method: str, _path: str, body: dict) -> dict:
            outgoing.append(body)
            return {"data": {"message_id": uuid4().hex}}

        monkeypatch.setattr(adapter, "_request", request)
        try:

            async def send() -> None:
                incoming = await adapter._event(
                    json.dumps(
                        {
                            "header": {
                                "event_type": "im.message.receive_v1",
                                "event_id": uuid4().hex,
                            },
                            "event": {
                                "sender": {
                                    "sender_type": "user",
                                    "sender_id": {"open_id": "synthetic-user"},
                                },
                                "message": {
                                    "message_type": "text",
                                    "chat_type": "p2p",
                                    "chat_id": "synthetic-chat",
                                    "content": json.dumps({"text": "帮我按能力找人"}),
                                },
                            },
                        }
                    )
                )
                assert incoming
                await gateway.Gateway(engine).handle(
                    incoming,
                    "synthetic-app",
                    setting.credentials,
                    adapter,
                )

            await send()
            assert_public(results[0], expected, links=True)
            assert {r["employee_code"] for r in results[0]["data"]} == {"E01", "E03"}
            if invalid == "unbound":
                binding.unbind(db, p, "feishu")
            elif invalid == "inactive":
                employees["E01"].employment_status = "inactive"
                db.add(employees["E01"])
                db.commit()
            if invalid:
                await send()
                assert len(results) == 1
                assert binding.GUIDE in outgoing[-1]["content"]
        finally:
            await adapter.close()

    asyncio.run(exercise())
    assert "SECRET" not in json.dumps(outgoing)


def test_find_people_uses_shared_input() -> None:
    manager_tool = next(t for t in MANAGER_TOOLS if t.name == "find_people")
    assert manager_tool.input_model is PeopleSearchInput
    assert tools.TEAM_INPUTS["find_people"] is PeopleSearchInput
    assert not manager_tool.writes and not manager_tool.bot_allowed


def test_real_pi_manager_find_people(
    people: tuple,
    db: Session,
    pi_service: PiHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    demo, capability, _, _ = people
    subject = demo[1]["E01"]
    p = principal(db, subject.org_id, subject.id)
    enable(db, p.org_id)
    monkeypatch.setattr(core, "model_config", lambda _: pi_service.config)
    pi_service.replies = [
        {
            "calls": [
                {
                    "name": "find_people",
                    "arguments": {"capability_key": capability["key"]},
                }
            ]
        },
        "请查看匹配员工的画像",
    ]
    response = core.turn(db, p, store.create_session(db, p).id, "按能力找人")
    assert any(step.name == "按能力找人" and step.ok for step in response.steps)
    assert pi_service.requests


@pytest.mark.parametrize("field", ["org_id", "employee_id", "is_org_admin"])
def test_tool_arguments_cannot_change_identity(
    people: tuple, db: Session, client: TestClient, field: str
) -> None:
    demo, capability, _, foreign_department = people
    subject = demo[1]["E01"]
    p = principal(db, subject.org_id, subject.id)
    session = store.create_session(db, p)
    args = {
        "capability_key": capability["key"],
        field: True if field == "is_org_admin" else str(foreign_department.org_id),
    }
    result = tools.call(db, p, session, [], "find_people", args)
    assert set(result) == {"error"}
    headers, _ = token_for(db, subject)
    denied = call(client, initialize(client, headers), "find_people", args)
    assert denied["isError"]
    assert "structuredContent" not in denied
    assert headers["Authorization"].split()[1] not in json.dumps(denied)
