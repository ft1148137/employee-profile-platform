import json
import re
from datetime import timedelta

import httpx
import pytest
from fastapi import HTTPException
from sqlmodel import Session, select

from app.authz.principal import Principal
from app.core.secrets import SecretUnavailable
from app.domains.base import utc_now
from app.domains.capability.service import activate_template, list_capabilities
from app.domains.change_request.models import (
    ChangeItem,
    ChangeItemEvidence,
    ProfileChangeRequest,
)
from app.domains.drafting import model, service
from app.domains.drafting.collect import collect
from app.domains.drafting.models import DraftingSetting
from app.domains.evidence.models import Evidence, EvidenceKind, Visibility
from app.domains.evidence.service import append_evidence, retract_evidence
from app.domains.model.client import (
    ModelConfig,
    ModelRateLimited,
    ModelUnavailable,
    _sdk,
)
from app.domains.model.models import ModelSetting
from app.domains.org.models import ExternalIdentity
from app.domains.profile.models import ProfileProgress
from app.worker.tasks import draft_initial
from tests.api.routes.test_initial_profiles import work_queue
from tests.api.routes.test_model_setting import (
    completions as model_completions,  # noqa: F401
)
from tests.authz.test_tenant_access import tenant_data  # noqa: F401
from tests.domains.test_ingest import ingest, person, record, rows

pytestmark = pytest.mark.xdist_group("litellm")


def answer(**changes: object) -> str:
    return json.dumps(
        {
            "items": [
                {
                    "capability_key": "backend_api",
                    "statement": "能开发接口",
                    "level": "routine",
                    "evidence": ["R1"],
                    "explanation": "完成了接口相关工作。",
                    "uncertainty": "指派人不一定是实际完成人。",
                    **changes,
                }
            ]
        },
        ensure_ascii=False,
    )


@pytest.fixture
def drafting(request: pytest.FixtureRequest, db: Session) -> tuple:
    orgs, depts, employees, users = request.getfixturevalue("tenant_data")
    org = orgs[0].id
    schema = activate_template(db, Principal.system_worker(org), "tech-rnd")
    db.add(
        ModelSetting(
            org_id=org,
            updated_by_user_id=users[0].id,
            enabled=True,
            kind="cloud_api",
            model="openai/gpt-4o-mini",
            reasoning_effort="low",
        )
    )
    db.add_all(
        ExternalIdentity(
            org_id=org, employee_id=employees[0].id, source=s, external_id="alpha"
        )
        for s in ("jira", "csv:Git")
    )
    db.flush()
    ingest(db, org, record(person(), key="API-1", title="实现 API"))
    ingest(db, org, record(person(), source="csv:Git", key="GIT-1", title="对应API-1"))
    ingest(db, org, record(person(role="reporter"), key="API-2", title="报告接口问题"))
    progress = db.get(ProfileProgress, (org, employees[0].id))
    progress.inference_status = "generated"
    db.add(progress)
    employees[0].employee_code = "SYNTHETIC-EMPLOYEE-001"
    employees[0].work_email = "synthetic-employee@example.com"
    employees[0].title = "Synthetic job title"
    depts[0].name = "Synthetic department name"
    db.add_all([employees[0], depts[0]])
    db.commit()
    return org, schema, depts, employees, users


def test_model_request_and_privacy(
    drafting: tuple, db: Session, request: pytest.FixtureRequest
) -> None:
    org, schema, _, employees, _ = drafting
    replies, calls = request.getfixturevalue("model_completions")
    replies.append(answer(evidence=["R1", "R2", "R3"]))
    service.run_drafting(Principal.system_worker(org), [str(employees[0].id)])
    db.expire_all()
    item = rows(db, ChangeItem, org)[0]
    assert item.after["statement"] == "能开发接口" and item.after["level"] == "routine"
    assert item.after["assessment_status"] == "tentative"
    assert item.after["evidence_count"] == 1 and item.after["source_count"] == 2
    assert item.after["latest_evidence_at"].startswith("2026-01-01")
    assert item.explanation.startswith(
        "依据 1 项工作经历"
    ) and item.explanation.endswith("完成了接口相关工作。")
    assert item.uncertainty == "指派人不一定是实际完成人。"
    links = rows(db, ChangeItemEvidence, org)
    assert sorted(link.relation for link in links) == [
        "context",
        "supports",
        "supports",
    ]
    generated = rows(db, ProfileChangeRequest, org)[0].generated_by
    assert generated == {
        "mode": "model",
        "provider": "openai",
        "model": "gpt-4o-mini",
        "prompt_version": model.PROMPT_VERSION,
        "schema_version_id": str(schema.id),
        "keywords_ref": None,
        "reasoning_effort": "low",
    }
    payload = json.dumps(calls[0]["messages"], ensure_ascii=False)
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", payload)
    assert all(e.display_name not in payload for e in employees)
    assert "alpha" not in payload and "same@example.com" not in payload
    for value in (
        "SYNTHETIC-EMPLOYEE-001",
        "synthetic-employee@example.com",
        "Synthetic job title",
        "Synthetic department name",
    ):
        assert value not in payload
    body = json.loads(calls[0]["messages"][-1]["content"])
    assert {r["source"] for r in body["records"]} == {"Jira", "Git"}
    assert len({r["experience"] for r in body["records"]}) == 2
    assert all(r["date"] == "2026-01-01" for r in body["records"])


@pytest.mark.parametrize(
    "change",
    [
        {"evidence": ["R999"]},
        {"capability_key": "missing"},
        {"level": "expert"},
        {"evidence": []},
        {"statement": "x" * 201},
        {"explanation": " "},
        {"uncertainty": "x" * 301},
    ],
)
def test_invalid_output_retries(
    drafting: tuple, db: Session, request: pytest.FixtureRequest, change: dict
) -> None:
    org, _, _, employees, _ = drafting
    replies, calls = request.getfixturevalue("model_completions")
    replies.extend([answer(**change)] * 3 + ['{"items":[]}'])
    service.run_drafting(
        Principal.system_worker(org), [str(e.id) for e in employees[:2]]
    )
    db.expire_all()
    progress = service.read_progress(db, org)
    assert progress.counts["failed"] == 1 and progress.counts["generated"] == 1
    assert progress.failed[0].error == "模型输出不符合格式，已重试 3 次"
    assert len(calls) == 4 and len(calls[1]["messages"]) > len(calls[0]["messages"])
    replies.append(answer())
    assert service.queue_drafting(db, org, "failed") == 1
    work_queue()
    db.expire_all()
    assert service.read_progress(db, org).counts["failed"] == 0


def test_duplicate_capability_and_reporter(
    drafting: tuple, db: Session, request: pytest.FixtureRequest
) -> None:
    org, schema, _, employees, _ = drafting
    replies, calls = request.getfixturevalue("model_completions")
    records = collect(db, org, employees[0])
    reporter = next(r for r in records if r.evidence.content["roles"] == ["reporter"])
    caps = list_capabilities(db, org, schema.id)
    duplicate = json.loads(answer())
    duplicate["items"] *= 2
    replies.extend([json.dumps(duplicate), answer()])
    items, _ = model.draft([reporter], caps, ModelConfig("openai/gpt-4o-mini"))
    assert len(calls) == 2 and list(items[0].evidence.values()) == ["context"]
    assert service.postprocess(db, org, employees[0], items, caps) == []


def test_recent_records_and_restricted_input(
    drafting: tuple, db: Session, request: pytest.FixtureRequest
) -> None:
    org, schema, _, employees, _ = drafting
    for i in range(82):
        append_evidence(
            db,
            org_id=org,
            subject_employee_id=employees[0].id,
            kind=EvidenceKind.WORK_RECORD,
            source="csv:Git",
            source_ref=f"EXTRA-{i}",
            title=f"Record {i}",
            content={"detail": "x" * 1200, "email": "private@example.com"},
            visibility=Visibility.STANDARD,
            provenance={},
            occurred_at=utc_now() + timedelta(days=i),
        )
    restricted, _ = append_evidence(
        db,
        org_id=org,
        subject_employee_id=employees[0].id,
        kind=EvidenceKind.WORK_RECORD,
        source="csv:Git",
        source_ref="restricted",
        title="Restricted secret",
        content={},
        visibility=Visibility.RESTRICTED,
        provenance={},
        occurred_at=utc_now(),
    )
    stale = next(e for e in rows(db, Evidence, org) if e.source_ref == "API-1")
    retract_evidence(
        db,
        evidence=stale,
        reason="Synthetic",
        actor_type=Principal.system_worker(org).actor_type,
        actor_id=None,
    )
    db.flush()
    replies, calls = request.getfixturevalue("model_completions")
    replies.append('{"items":[]}')
    records = collect(db, org, employees[0])
    assert stale.id not in {r.evidence.id for r in records}
    assert restricted.id in {r.evidence.id for r in records}
    model.draft(
        records,
        list_capabilities(db, org, schema.id),
        ModelConfig("openai/gpt-4o-mini"),
    )
    body = json.loads(calls[0]["messages"][-1]["content"])
    assert len(body["records"]) == 80
    assert (
        body["records"][0]["title"] == "Record 81"
        and body["records"][-1]["title"] == "Record 2"
    )
    assert all(len(r["detail"]) == 1000 for r in body["records"])
    assert "Restricted secret" not in str(body) and "private@example.com" not in str(
        body
    )


@pytest.mark.parametrize("enabled", [True, False])
def test_scope_filters(
    drafting: tuple, db: Session, request: pytest.FixtureRequest, enabled: bool
) -> None:
    org, _, depts, employees, users = drafting
    replies, calls = request.getfixturevalue("model_completions")
    setting = db.get(ModelSetting, org)
    setting.enabled = enabled
    db.add(setting)
    db.add(
        DraftingSetting(
            org_id=org,
            excluded_capability_keys=["backend_api"],
            updated_by_user_id=users[0].id,
        )
    )
    employees[0].auto_draft_opt_out = True
    employees[3].department_id = depts[2].id
    depts[1].auto_draft_disabled = True
    depts[2].parent_department_id = depts[1].id
    for row in [employees[0], employees[3], depts[1], depts[2]]:
        db.add(row)
    db.add(
        ExternalIdentity(
            org_id=org, employee_id=employees[1].id, source="jira", external_id="beta"
        )
    )
    db.flush()
    ingest(db, org, record(person("beta"), key="BETA-1", title="API"))
    service.write_initial_request(db, org, employees[0], [], {"mode": "existing"})
    db.commit()
    existing_progress = db.get(ProfileProgress, (org, employees[0].id)).inference_status
    replies.extend([answer(), '{"items":[]}', '{"items":[]}'])
    assert service.queue_drafting(db, org, "all") == 1
    # Explicit worker IDs must respect the same switches as queueing.
    service.run_drafting(Principal.system_worker(org), [str(e.id) for e in employees])
    db.expire_all()
    requests = rows(db, ProfileChangeRequest, org)
    assert len(requests) == 2
    assert next(
        r for r in requests if r.subject_employee_id == employees[0].id
    ).generated_by == {"mode": "existing"}
    assert (
        db.get(ProfileProgress, (org, employees[0].id)).inference_status
        == existing_progress
    )
    progress = service.read_progress(db, org)
    assert progress.counts["excluded"] == 4 and progress.counts["not_started"] == 0
    assert not rows(db, ChangeItem, org)
    if enabled:
        body = json.loads(calls[0]["messages"][-1]["content"])
        assert "backend_api" not in {c["key"] for c in body["capabilities"]}
        assert len(calls) == 2
    else:
        assert calls == []
    # Finish the queued task so no worker job escapes this test.
    work_queue()


@pytest.mark.parametrize("disable", [True, False])
def test_pause_and_resume(
    drafting: tuple, db: Session, request: pytest.FixtureRequest, disable: bool
) -> None:
    org, _, _, employees, _ = drafting
    replies, _ = request.getfixturevalue("model_completions")
    error = _sdk().RateLimitError(
        "limited",
        "openai",
        "gpt-4o-mini",
        response=httpx.Response(
            429,
            headers={"Retry-After": "3600"},
            request=httpx.Request("POST", "https://example.com"),
        ),
    )
    replies.extend([answer(), error, error, error])
    assert service.queue_drafting(db, org, "all") == 5
    work_queue()
    db.expire_all()
    progress = service.read_progress(db, org)
    assert progress.counts["generated"] == 1 and progress.counts["pending"] == 4
    assert progress.paused_until > utc_now() and progress.pause_reason
    pending = db.exec(
        select(ProfileProgress).where(
            ProfileProgress.org_id == org, ProfileProgress.inference_status == "pending"
        )
    ).all()
    assert all("自动继续" in p.inference_error for p in pending)
    with pytest.raises(HTTPException) as raised:
        service.queue_drafting(db, org, "all")
    assert raised.value.status_code == 409
    db.rollback()
    from sqlalchemy import text

    job = db.execute(
        text(
            "SELECT args, scheduled_at, lock, queueing_lock FROM procrastinate_jobs WHERE task_name='profiles.draft_initial' AND status='todo' AND args->>'org_id'=:org"
        ),
        {"org": str(org)},
    ).one()
    assert set(job.args["employee_ids"]) == {str(p.employee_id) for p in pending}
    assert job.scheduled_at == progress.paused_until
    assert job.lock == f"initial-profiles:{org}" and job.queueing_lock == f"draft:{org}"
    setting = db.get(ModelSetting, org)
    setting.enabled = not disable
    db.add(setting)
    db.execute(
        text(
            "UPDATE procrastinate_jobs SET scheduled_at=now() WHERE task_name='profiles.draft_initial' AND status='todo' AND args->>'org_id'=:org"
        ),
        {"org": str(org)},
    )
    db.commit()
    replies.extend(['{"items":[]}'] * 4)
    work_queue()
    db.expire_all()
    progress = service.read_progress(db, org)
    assert progress.counts["generated"] == 5 and progress.counts["pending"] == 0
    assert progress.paused_until is None and progress.pause_reason is None
    assert {r.generated_by["mode"] for r in rows(db, ProfileChangeRequest, org)} == (
        {"rules", "model"} if disable else {"model"}
    )


@pytest.mark.parametrize(
    "error,message",
    [
        (
            ModelUnavailable("ChatGPT 登录已失效，请在设置页重新连接"),
            "ChatGPT 登录已失效，请在设置页重新连接",
        ),
        (ModelUnavailable("模型服务暂时不可用"), "连不上模型服务：模型服务暂时不可用"),
        (SecretUnavailable(), "保存的模型密钥已失效，请重新填写"),
    ],
)
def test_failures(
    drafting: tuple,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    message: str,
) -> None:
    org, _, _, employees, _ = drafting

    def fail(*_args: object) -> None:
        raise error

    monkeypatch.setattr(model, "chat_json", fail)
    service.run_drafting(Principal.system_worker(org), [str(employees[0].id)])
    db.expire_all()
    assert service.read_progress(db, org).failed[0].error == message


def test_pause_without_retry_time(drafting: tuple, db: Session) -> None:
    org, _, _, employees, _ = drafting
    start = utc_now()
    service.pause_drafting(org, [employees[0].id], ModelRateLimited(None, "限流"))
    db.expire_all()
    assert (
        start + timedelta(minutes=30)
        <= db.get(ModelSetting, org).paused_until
        <= utc_now() + timedelta(minutes=30)
    )
    # Consume the scheduled job without invoking a provider.
    setting = db.get(ModelSetting, org)
    setting.enabled = False
    db.add(setting)
    from sqlalchemy import text

    db.execute(
        text(
            "UPDATE procrastinate_jobs SET scheduled_at=now() WHERE task_name='profiles.draft_initial' AND status='todo' AND args->>'org_id'=:org"
        ),
        {"org": str(org)},
    )
    db.commit()
    work_queue()


def test_chatgpt_separate_setting_lock(
    drafting: tuple,
    db: Session,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy import create_engine

    from app.core.db import engine
    from app.core.secrets import encrypt_secret
    from app.domains.model import chatgpt
    from tests.api.routes.test_chatgpt import record as synthetic_auth

    org, _, _, employees, _ = drafting
    replies, _ = request.getfixturevalue("model_completions")
    replies.append(answer())
    setting = db.get(ModelSetting, org)
    setting.kind, setting.model = "chatgpt_subscription", "chatgpt/gpt-6.1-sol"
    setting.chatgpt_auth_ciphertext = encrypt_secret(json.dumps(synthetic_auth()))
    setting.paused_until, setting.pause_reason = utc_now(), "previous limit"
    db.add(setting)
    db.commit()
    isolated = create_engine(
        engine.url, connect_args={"options": "-c lock_timeout=1000"}
    )
    monkeypatch.setattr(chatgpt, "engine", isolated)
    try:
        service.run_drafting(Principal.system_worker(org), [str(employees[0].id)])
        db.expire_all()
        assert service.read_progress(db, org).counts["failed"] == 0
        generated = rows(db, ProfileChangeRequest, org)[0].generated_by
        assert (
            generated["provider"] == "chatgpt" and generated["model"] == "gpt-6.1-sol"
        )
    finally:
        isolated.dispose()


@pytest.mark.parametrize("enabled", [True, False])
def test_opt_out_after_queue_does_not_block_next_batch(
    drafting: tuple, db: Session, request: pytest.FixtureRequest, enabled: bool
) -> None:
    org, _, _, employees, _ = drafting
    replies, _ = request.getfixturevalue("model_completions")
    replies.extend(['{"items":[]}'] * 9)
    setting = db.get(ModelSetting, org)
    setting.enabled = enabled
    db.add(setting)
    db.commit()
    draft_initial.configure(
        connection=db.connection().connection.driver_connection,
        lock=f"initial-profiles:{employees[-1].org_id}",
    ).defer(org_id=str(employees[-1].org_id), employee_ids=[])
    db.commit()
    assert service.queue_drafting(db, org, "all") == 5
    employees[0].auto_draft_opt_out = True
    db.add(employees[0])
    db.commit()
    with pytest.raises(HTTPException) as raised:
        service.queue_drafting(db, org, "all")
    assert raised.value.status_code == 409
    db.rollback()
    work_queue()
    db.expire_all()
    assert db.get(ProfileProgress, (org, employees[0].id)).inference_status == "pending"
    employees[0].auto_draft_opt_out = False
    db.add(employees[0])
    db.commit()
    assert service.queue_drafting(db, org, "all") == 5
    work_queue()
    db.expire_all()
    assert service.read_progress(db, org).counts["generated"] == 5
