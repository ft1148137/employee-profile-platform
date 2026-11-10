import asyncio
import json
import re
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlmodel import Session, select

from app.authz.deps import get_org_admin, get_org_principal
from app.authz.principal import Principal
from app.domains.admin_assistant import registry as r
from app.domains.org.models import Organization
from app.models import User


def principal(db: Session, demo: tuple, role: str = "admin") -> Principal:
    user = db.exec(
        select(User).where(
            User.email
            == {
                "admin": "admin@demo.example.com",
                "employee": "employee.platform@demo.example.com",
            }[role]
        )
    ).one()
    return get_org_principal(next(iter(demo[1].values())).org_id, user, db)


def call(
    db: Session, p: Principal, name: str, args: dict[str, Any] | None = None
) -> dict[str, Any]:
    return asyncio.run(r.admin_read(db, p, name, args or {}))


@pytest.mark.parametrize(
    "name", [n for n, f in r.FUNCTIONS.items() if f.kind == "read"]
)
def test_web_parity(
    db: Session, shared_demo: tuple, client: TestClient, name: str
) -> None:
    url, employees, auth = shared_demo
    entry = r.FUNCTIONS[name]
    args = {}
    if name == "get_employee_profile":
        args = {"employee_id": str(employees["E02"].id)}
    if name == "get_appeal":
        args = {"appeal_id": str(uuid4())}
    if name == "test_im_connection":
        args = {"platform": "feishu"}
    result = call(db, principal(db, shared_demo), name, args)
    expected = []
    for method, path in entry.endpoints:
        path = path.format(**args)
        response = client.request(method, url + path, headers=auth["admin"])
        if response.status_code == 404:
            assert result == {"data": {"error": "找不到该记录"}}
            return
        assert response.status_code == 200, response.text
        expected.append(response.json())
    web = expected[0]
    if name == "get_progress":
        web = {"sync_runs": expected[0], "drafting": expected[1]}
    if name == "get_employee_profile":
        for item in web["items"]:
            for ref in (item["claim"] or {}).get("evidence", []):
                ref["title"] = {
                    "work_record": "工作记录",
                    "self_report": "员工自述",
                    "manager_review": "经理评价",
                }[ref["kind"]]
    assert result["data"] == web


def test_boundaries(
    db: Session, shared_demo: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = principal(db, shared_demo)
    for name in r.FUNCTIONS:
        assert "error" in call(db, principal(db, shared_demo, "employee"), name)["data"]
        if r.FUNCTIONS[name].kind != "read":
            assert "不是只读" in call(db, p, name)["data"]["error"]
            continue
        for key in ("org_id", "user_id"):
            assert (
                "参数不正确" in call(db, p, name, {key: str(uuid4())})["data"]["error"]
            )
    foreign = db.exec(select(Organization).where(Organization.id != p.org_id)).first()
    assert foreign
    for employee_id in (uuid4(), next(iter(shared_demo[1].values())).id):
        wrong = replace(p, org_id=foreign.id)
        assert call(
            db, wrong, "get_employee_profile", {"employee_id": str(employee_id)}
        ) == {"data": {"error": "找不到该记录"}}
    monkeypatch.setitem(
        r.FUNCTIONS,
        "change",
        replace(r.FUNCTIONS["get_today"], name="change", kind="change"),
    )
    for name in ("change", "missing"):
        assert "不是只读" in call(db, p, name)["data"]["error"]
    for area in ("home", "people", "rules", "data_ai", "connections", "more"):
        functions = r.list_admin_functions(area)["data"]
        assert functions
        assert all(f["input_model"]["additionalProperties"] is False for f in functions)


def test_table_limit() -> None:
    rows = [{"名称": str(n)} for n in range(25)]
    card = r._table(r.FUNCTIONS["list_accounts"], {"data": rows, "count": 123})
    assert card and len(card.rows) == 20 and card.total == 123
    assert card.columns == ["名称"] and card.page == "/admin/people?tab=accounts"
    assert r._table(r.FUNCTIONS["list_roles"], []).total == 0


def test_endpoint_coverage() -> None:
    from app.api.main import api_router

    def walk(routes: list[Any]) -> Iterator[APIRoute]:
        for route in routes:
            if isinstance(route, APIRoute):
                yield route
            elif hasattr(route, "original_router"):
                yield from walk(route.original_router.routes)

    def admin(dep: Dependant) -> bool:
        return dep.call == get_org_admin or any(admin(d) for d in dep.dependencies)

    assert not r.PENDING
    registered = [e for f in r.FUNCTIONS.values() for e in f.endpoints]
    classified = registered + list(r.NOT_FOR_ASSISTANT) + list(r.PENDING)
    assert len(classified) == len(set(classified))
    routes = list(walk(api_router.routes))
    endpoints = {
        (m, route.path.removeprefix("/orgs/{org_id}"))
        for route in routes
        for m in route.methods
    }
    assert set(classified) <= endpoints
    for route in routes:
        # These two route groups authorize inside the account service.
        service_admin = route.endpoint.__module__ in (
            "app.api.routes.accounts",
            "app.api.routes.roles",
        )
        if admin(route.dependant) or service_admin:
            for method in route.methods:
                assert (method, route.path.removeprefix("/orgs/{org_id}")) in classified
    assert all(r.NOT_FOR_ASSISTANT.values()) and all(r.PENDING.values())


def test_function_map_covers_registry() -> None:
    guide = Path(__file__).resolve().parents[3] / "docs/admin-function-map.md"
    documented = set(re.findall(r"`([a-z][a-z0-9_]*)`", guide.read_text()))
    assert set(r.FUNCTIONS) <= documented


def test_secret_and_title_canaries(
    db: Session, demo: tuple, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.secrets import encrypt_secret
    from app.domains.access_token.models import PersonalAccessToken
    from app.domains.evidence.models import EvidenceKind, Visibility
    from app.domains.evidence.service import append_evidence
    from app.domains.im.models import ImSetting
    from app.domains.model.models import ModelSetting
    from app.domains.profile.models import ClaimEvidence, ProfileClaim
    from app.domains.source.models import SourceConnection

    p = principal(db, demo)
    secret = "synthetic-secret-canary-271"
    db.add(
        ModelSetting(
            org_id=p.org_id,
            updated_by_user_id=p.user_id,
            api_key_ciphertext=encrypt_secret(secret),
        )
    )
    db.add(
        SourceConnection(
            org_id=p.org_id,
            kind="jira",
            updated_by_user_id=p.user_id,
            secret_ciphertext=encrypt_secret(secret),
            config={
                "deployment": "cloud",
                "base_url": "https://synthetic.atlassian.net",
                "auth_type": "api_token",
                "username": "admin@example.com",
            },
        )
    )
    db.add(
        ImSetting(
            org_id=p.org_id,
            platform="feishu",
            updated_by_user_id=p.user_id,
            credentials=encrypt_secret(
                json.dumps({"app_id": "synthetic", "app_secret": secret})
            ),
        )
    )
    # MCP stores only a digest, never plaintext.
    from datetime import timedelta
    from hashlib import sha256

    from app.domains.base import utc_now

    employee_principal = principal(db, demo, "employee")
    db.add(
        PersonalAccessToken(
            org_id=p.org_id,
            user_id=employee_principal.user_id,
            employee_id=employee_principal.employee_id,
            name="synthetic",
            token_hash=sha256(secret.encode()).hexdigest(),
            prefix="synthetic",
            expires_at=utc_now() + timedelta(days=1),
        )
    )
    assert "token" not in r.access.get_setting(db, p).model_dump()
    employees = demo[1]
    web = client.get(
        demo[0] + f"/employees/{employees['E02'].id}/profile", headers=demo[2]["admin"]
    ).json()
    current_claim_id = next(
        item["claim"]["id"] for item in web["items"] if item["claim"]
    )
    claim = db.exec(
        select(ProfileClaim).where(
            ProfileClaim.id == current_claim_id,
            ProfileClaim.org_id == p.org_id,
            ProfileClaim.subject_employee_id == employees["E02"].id,
        )
    ).first()
    assert claim
    title = "synthetic-evidence-canary-271"
    for kind in EvidenceKind:
        evidence, _ = append_evidence(
            db,
            org_id=p.org_id,
            subject_employee_id=claim.subject_employee_id,
            kind=kind,
            source="jira" if kind == EvidenceKind.WORK_RECORD else "manual",
            source_ref=str(uuid4()),
            title=title,
            content={"description": title},
            visibility=Visibility.STANDARD,
            provenance={},
        )
        db.add(
            ClaimEvidence(
                org_id=p.org_id,
                claim_id=claim.id,
                evidence_id=evidence.id,
                relation="supports",
            )
        )
    db.commit()
    # Deterministic transports; saved credentials must never reach public outputs.
    monkeypatch.setattr(
        r.sources,
        "check_jira",
        lambda *_: r.sources.JiraConnectionTest(ok=True, message="连接成功"),
    )
    monkeypatch.setattr(
        r.model,
        "check_connection",
        lambda *_: r.model.ConnectionTest(ok=True, message="连接成功"),
    )

    async def im_ok(*_: Any) -> BaseModel:
        from app.domains.im.schemas import ImConnectionTest

        return ImConnectionTest(ok=True, message="连接成功")

    monkeypatch.setattr(r.im, "test_connection", im_ok)
    for name in r.FUNCTIONS:
        args = (
            {"employee_id": str(claim.subject_employee_id)}
            if name == "get_employee_profile"
            else {"appeal_id": str(uuid4())}
            if name == "get_appeal"
            else {"platform": "feishu"}
            if name == "test_im_connection"
            else {}
        )
        result = call(db, p, name, args)
        assert secret not in json.dumps(result, ensure_ascii=False)
        assert title not in json.dumps(result, ensure_ascii=False)
    response = client.get(
        demo[0] + f"/employees/{claim.subject_employee_id}/profile",
        headers=demo[2]["admin"],
    )
    assert response.status_code == 200 and title in response.text


def test_existing_appeal(db: Session, demo: tuple, client: TestClient) -> None:
    from app.domains.change_request.models import ItemDecision
    from app.domains.review import service
    from tests.api.routes.test_reviews import manager, pending, values

    owner, request = pending(db, demo)
    service.decide(
        manager(db, demo),
        db,
        request.id,
        request.revision,
        values(request, ItemDecision.REJECTED),
    )
    record = r.appeal.appeal_decision(
        owner, db, request.id, request.items[0].id, "合成申诉"
    )
    db.commit()
    expected = client.get(demo[0] + f"/appeals/{record.id}", headers=demo[2]["admin"])
    assert expected.status_code == 200
    assert (
        call(db, principal(db, demo), "get_appeal", {"appeal_id": str(record.id)})[
            "data"
        ]
        == expected.json()
    )
