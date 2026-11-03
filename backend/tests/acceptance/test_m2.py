"""M2 acceptance across real domain services and in-process Jira transports."""

import json
import runpy
from collections.abc import Generator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session, select

from app.authz.principal import Principal
from app.connectors.jira.client import jira_client_for
from app.connectors.jira.mock import create_mock_app
from app.core.security import create_access_token
from app.domains.base import utc_now
from app.domains.capability.models import CapabilityDefinition
from app.domains.change_request.models import (
    ChangeItem,
    ChangeItemEvidence,
    ProfileChangeRequest,
)
from app.domains.drafting import service
from app.domains.drafting.collect import evidence_stats
from app.domains.evidence.models import Evidence
from app.domains.model.client import ModelRateLimited, _sdk
from app.domains.model.models import ModelSetting
from app.domains.org.models import Department, OrgMembership, RoleGrant
from app.domains.profile.models import ProfileClaim
from app.domains.source import sync
from app.domains.work.models import Task, WorkEvent
from app.models import User
from tests.api.routes.test_initial_profiles import work_queue

ROOT = Path(__file__).resolve().parents[3]
evaluation = runpy.run_path(str(ROOT / "examples/test-enterprises/evaluate_drafts.py"))


@pytest.fixture
def enterprise(
    db: Session,
    superuser_token_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[tuple]:
    with evaluation["enterprise"]("demo-org") as (session, org, employees):
        admin = db.exec(select(User).where(User.is_superuser)).first()
        assert admin
        membership = OrgMembership(org_id=org.id, user_id=admin.id)
        session.add(membership)
        session.flush()
        session.add(
            RoleGrant(org_id=org.id, membership_id=membership.id, role="org_admin")
        )
        session.commit()
        http = TestClient(create_mock_app(ROOT / "examples"))
        monkeypatch.setattr(sync, "jira_client_for", lambda c: jira_client_for(c, http))
        yield (
            session,
            org,
            employees,
            f"/api/v1/orgs/{org.id}",
            superuser_token_headers,
            admin,
        )
        http.close()
        session.rollback()
        session.execute(
            text("DELETE FROM procrastinate_jobs WHERE args->>'org_id'=:org"),
            {"org": str(org.id)},
        )
        session.commit()


def rows(db: Session, model: Any, org: UUID) -> list[Any]:
    db.expire_all()
    return list(db.exec(select(model).where(model.org_id == org)).all())


def synchronize(context: tuple, client: TestClient, deployment: str) -> dict:
    _, _, _, base, auth, _ = context
    response = client.put(
        base + "/sources/jira",
        headers=auth,
        json={
            "deployment": deployment,
            "base_url": "http://testserver/demo-org",
            "auth_type": "api_token" if deployment == "cloud" else "pat",
            "username": "jira-admin@example.com",
            "secret": "mock-cloud-token" if deployment == "cloud" else "mock-dc-pat",
            "start_date": "2020-01-01",
        },
    )
    assert response.status_code == 200
    run = client.post(base + "/sources/jira/sync", headers=auth)
    assert run.status_code == 202
    work_queue()
    result = client.get(base + "/sync-runs", headers=auth).json()["data"][0]
    return result


def link_candidates(context: tuple, client: TestClient) -> None:
    _, _, _, base, auth, _ = context
    identities = client.get(
        base + "/external-identities?limit=200", headers=auth
    ).json()["data"]
    links = [
        {"identity_id": i["id"], "employee_id": i["candidate"]["id"]}
        for i in identities
        if i["candidate"]
    ]
    assert links
    assert (
        client.post(
            base + "/external-identities/link", headers=auth, json={"links": links}
        ).status_code
        == 200
    )


@pytest.mark.parametrize("deployment", ["cloud", "data_center"])
def test_sync_draft_repeat_migrate(
    enterprise: tuple,
    client: TestClient,
    deployment: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, org, employees, base, auth, _ = enterprise
    if deployment == "cloud":
        # Fail the first full synchronization after its first committed page.
        from app.worker import tasks

        ingest, calls = sync.ingest_record, 0

        def interrupted(*args: Any, **kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            if calls == 12:
                raise RuntimeError("Synthetic interruption")
            return ingest(*args, **kwargs)

        monkeypatch.setattr(sync, "ingest_record", interrupted)
        monkeypatch.setattr(
            tasks,
            "execute_sync",
            lambda p, r, a: sync.execute_sync(p, r, a, page_size=10),
        )
        failed = synchronize(enterprise, client, deployment)
        assert failed["status"] == "failed" and failed["counts"]["records"] == 10
        monkeypatch.setattr(sync, "ingest_record", ingest)
        assert (
            client.post(
                base + f"/sync-runs/{failed['id']}/retry", headers=auth
            ).status_code
            == 202
        )
        work_queue()
        recovered = client.get(base + "/sync-runs", headers=auth).json()["data"][0]
        assert (
            recovered["status"] == "succeeded" and recovered["counts"]["records"] == 37
        )
    else:
        assert synchronize(enterprise, client, deployment)["status"] == "succeeded"
    assert len(rows(db, Task, org.id)) == 37
    link_candidates(enterprise, client)
    # PLAT-999 has an unregistered assignee; that role must remain uncounted.
    assert len(rows(db, WorkEvent, org.id)) == 73
    service.run_drafting(Principal.system_worker(org.id), None)
    requests = rows(db, ProfileChangeRequest, org.id)
    assert len(requests) == len(employees) == 12
    assert len({r.subject_employee_id for r in requests}) == 12
    assert all(r.status == "pending_owner" for r in requests)
    items = rows(db, ChangeItem, org.id)
    assert items
    for request in requests:
        user = User(username=f"m2-{request.id}", hashed_password="unused")
        db.add(user)
        db.flush()
        db.add(
            OrgMembership(
                org_id=org.id, user_id=user.id, employee_id=request.subject_employee_id
            )
        )
        db.commit()
        owner_auth = {
            "Authorization": f"Bearer {create_access_token(user.id, expires_delta=timedelta(minutes=5))}"
        }
        for item in [i for i in items if i.request_id == request.id]:
            links = db.exec(
                select(ChangeItemEvidence).where(
                    ChangeItemEvidence.org_id == org.id,
                    ChangeItemEvidence.item_id == item.id,
                    ChangeItemEvidence.relation == "supports",
                )
            ).all()
            assert links
            stats = evidence_stats(db, org.id, [link.evidence_id for link in links])
            assert item.after["evidence_count"] == stats.evidence_count
            assert item.after["source_count"] == stats.source_count
            assert (
                datetime.fromisoformat(item.after["latest_evidence_at"])
                == stats.latest_evidence_at
            )
            assert item.explanation.startswith(
                f"依据 {stats.evidence_count} 项工作经历"
            )
            assert f"来自 {stats.source_count} 个来源" in item.explanation
            assert (
                str(
                    stats.latest_evidence_at.astimezone(
                        ZoneInfo("Asia/Shanghai")
                    ).date()
                )
                in item.explanation
            )
            for link in links:
                evidence = db.get(Evidence, link.evidence_id)
                assert (
                    evidence.subject_employee_id == request.subject_employee_id
                    and evidence.status == "active"
                )
                response = client.get(
                    base + f"/evidence/{evidence.id}", headers=owner_auth
                )
                assert response.status_code == 200 and not response.json()["redacted"]
    before = [len(rows(db, m, org.id)) for m in (Task, WorkEvent, Evidence, ChangeItem)]
    repeated = synchronize(enterprise, client, deployment)
    assert repeated["status"] == "succeeded"
    assert all(
        repeated["counts"][key] == 0
        for key in ("tasks_added", "events_added", "evidence_added")
    )
    assert [
        len(rows(db, m, org.id)) for m in (Task, WorkEvent, Evidence, ChangeItem)
    ] == before
    if deployment == "data_center":
        assert synchronize(enterprise, client, "cloud")["status"] == "succeeded"
        link_candidates(enterprise, client)
        service.run_drafting(Principal.system_worker(org.id), None)
        assert len(rows(db, Evidence, org.id)) == before[2]
        assert {r.id for r in rows(db, ProfileChangeRequest, org.id)} == {
            r.id for r in requests
        }


@pytest.mark.parametrize("model", [False, True])
def test_switches_pause_resume(
    enterprise: tuple, client: TestClient, monkeypatch: pytest.MonkeyPatch, model: bool
) -> None:
    db, org, employees, base, auth, admin = enterprise
    assert synchronize(enterprise, client, "cloud")["status"] == "succeeded"
    link_candidates(enterprise, client)
    # Mock only the transport; LiteLLM still builds and parses its normal response.
    sdk, real = _sdk(), _sdk().completion

    def completion(**kwargs: Any) -> Any:
        body = json.loads(kwargs["messages"][-1]["content"])
        return real(
            **kwargs,
            mock_response=json.dumps(
                {
                    "items": [
                        {
                            "capability_key": body["capabilities"][0]["key"],
                            "statement": "合成能力建议",
                            "level": None,
                            "evidence": [r["ref"] for r in body["records"]],
                            "explanation": "合成工作记录",
                            "uncertainty": "需要本人确认",
                        }
                    ]
                }
            ),
        )

    monkeypatch.setattr(sdk, "completion", completion)
    db.add(
        ModelSetting(
            org_id=org.id,
            updated_by_user_id=admin.id,
            enabled=model,
            kind="cloud_api",
            model="openai/gpt-4o-mini",
        )
    )
    departments = rows(db, Department, org.id)
    departments[1].parent_department_id = departments[0].id
    db.add(departments[1])
    people = list(employees.values())
    opted = next(
        e for e in people if e.department_id not in {d.id for d in departments[:2]}
    )
    owner = User(username=f"m2-switch-{org.id}", hashed_password="unused")
    db.add(owner)
    db.flush()
    db.add(OrgMembership(org_id=org.id, user_id=owner.id, employee_id=opted.id))
    capability = db.exec(
        select(CapabilityDefinition).where(
            CapabilityDefinition.org_id == org.id,
            CapabilityDefinition.key == "backend_api",
        )
    ).one()
    db.add(
        ProfileClaim(
            org_id=org.id,
            subject_employee_id=opted.id,
            capability_definition_id=capability.id,
            statement="Existing synthetic judgment",
            assessment_status="tentative",
            generated_by={},
        )
    )
    db.commit()
    owner_auth = {
        "Authorization": f"Bearer {create_access_token(owner.id, expires_delta=timedelta(minutes=5))}"
    }
    snapshot = [
        [r.model_dump(mode="json") for r in rows(db, m, org.id)]
        for m in (Evidence, ProfileClaim)
    ]
    assert (
        client.put(
            base + "/me/auto-draft", headers=owner_auth, json={"enabled": False}
        ).status_code
        == 200
    )
    response = client.put(
        base + "/drafting-settings",
        headers=auth,
        json={
            "excluded_capability_keys": ["backend_api"],
            "disabled_department_ids": [str(departments[0].id)],
        },
    )
    assert response.status_code == 200
    service.run_drafting(Principal.system_worker(org.id), None)
    requests = rows(db, ProfileChangeRequest, org.id)
    excluded = {
        e.id for e in people if e.department_id in {d.id for d in departments[:2]}
    } | {opted.id}
    assert {r.subject_employee_id for r in requests} == {
        e.id for e in people
    } - excluded
    for item in rows(db, ChangeItem, org.id):
        assert (
            db.get(CapabilityDefinition, item.capability_definition_id).key
            != "backend_api"
        )
    assert snapshot == [
        [r.model_dump(mode="json") for r in rows(db, m, org.id)]
        for m in (Evidence, ProfileClaim)
    ]
    assert (
        client.put(
            base + "/me/auto-draft", headers=owner_auth, json={"enabled": True}
        ).status_code
        == 200
    )
    if model:
        original = service.model.draft

        def limited(*_args: Any) -> Any:
            raise ModelRateLimited(utc_now() + timedelta(hours=1), "合成用量上限")

        monkeypatch.setattr(service.model, "draft", limited)
        service.run_drafting(Principal.system_worker(org.id), [str(opted.id)])
        assert service.read_progress(db, org.id).paused_until > utc_now()
        monkeypatch.setattr(service.model, "draft", original)
        db.execute(
            text(
                "UPDATE procrastinate_jobs SET scheduled_at=now() WHERE args->>'org_id'=:org AND status='todo'"
            ),
            {"org": str(org.id)},
        )
        db.commit()
        work_queue()
    else:
        service.run_drafting(Principal.system_worker(org.id), [str(opted.id)])
    assert opted.id in {
        r.subject_employee_id for r in rows(db, ProfileChangeRequest, org.id)
    }
    if model:
        assert all(
            r.generated_by["provider"] == "openai"
            and r.generated_by["model"] == "gpt-4o-mini"
            for r in rows(db, ProfileChangeRequest, org.id)
        )


def test_csv_participants_and_explicit_cross_source_reference() -> None:
    from app.domains.drafting.collect import collect
    from app.domains.org.models import ExternalIdentity
    from app.domains.source.sync import csv_record
    from app.domains.work.ingest import ingest_record
    from app.domains.work.records import parse_work_records

    with evaluation["enterprise"]("qiguang-aiot") as (db, org, employees):
        evaluation["ingest_dataset"](db, org, employees, "qiguang-aiot")
        identities = rows(db, ExternalIdentity, org.id)
        service_account = next(i for i in identities if i.external_id == "plm.svc.sync")
        assert service_account.employee_id is None
        evidence = rows(db, Evidence, org.id)
        assert not any(e.source_ref == "PLM-0031" for e in evidence)
        assert any(
            e.subject_employee_id == employees["QG002"].id
            and e.source_ref == "PLM-0001"
            and "主讲人" in str(e.content)
            for e in evidence
        )
        # The reference directly touches Chinese text, exercising NERE-79's fix.
        csv = "记录编号,来源,发生时间,执行人账号,标题\nPLM-9999,PLM,2026-10-01T00:00:00+08:00,synthetic.review19,对应FW-215\n"
        parsed = parse_work_records(csv.encode(), "synthetic.csv")
        assert not parsed.has_errors
        db.add(
            ExternalIdentity(
                org_id=org.id,
                employee_id=employees["QG019"].id,
                source="csv:PLM",
                external_id="synthetic.review19",
            )
        )
        db.flush()
        ingest_record(
            db, org_id=org.id, record=csv_record(parsed.rows[0]), provenance={}
        )
        db.commit()
        records = collect(db, org.id, employees["QG019"])
        pair = [r for r in records if r.evidence.source_ref in {"FW-215", "PLM-9999"}]
        assert len(pair) == 2
        stats = evidence_stats(db, org.id, [r.evidence.id for r in pair])
        assert stats.evidence_count == 1 and stats.source_count == 2
