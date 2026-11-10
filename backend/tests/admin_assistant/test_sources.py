import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session, select

from app.connectors.jira.client import SourceAuthError
from app.core.secrets import decrypt_secret
from app.domains.admin_assistant import change_set as c
from app.domains.admin_assistant import core, registry, store
from app.domains.admin_assistant.models import AdminChangeSet
from app.domains.admin_assistant.schemas import AdminTurnInput
from app.domains.audit.models import AuditEvent
from app.domains.bot import store as messages
from app.domains.model.pi_client import TurnResult
from app.domains.org.models import ExternalIdentity
from app.domains.source import service
from app.domains.source.models import SyncRun
from app.domains.source.schemas import JiraConnectionUpdate
from tests.admin_assistant.test_change_set import proposal
from tests.admin_assistant.test_registry import principal
from tests.domains.test_bot_core import enable

CONFIG = {
    "deployment": "cloud",
    "base_url": "https://synthetic.atlassian.net",
    "auth_type": "api_token",
    "username": "synthetic@example.com",
    "start_date": "2026-01-01",
}
CANARY = "sk-synthetic-jira-canary-264"


def setup(db: Session, demo: tuple) -> ExternalIdentity:
    p = principal(db, demo)
    service.save_jira(
        db,
        p,
        JiraConnectionUpdate.model_validate(
            CONFIG | {"secret": "old-synthetic-secret"}
        ),
    )
    identity = ExternalIdentity(org_id=p.org_id, source="jira", external_id="synthetic")
    db.add(identity)
    db.commit()
    return identity


def draft(db: Session, demo: tuple, args: dict) -> AdminChangeSet:
    return proposal(
        db, demo, items=[{"function": "update_jira_connection", "args": CONFIG | args}]
    )


def test_duplicate_credential_targets_are_rejected(db: Session, demo: tuple) -> None:
    with pytest.raises(c.ChangeSetError, match="不能重复配置连接"):
        proposal(
            db,
            demo,
            items=[
                {"function": "update_jira_connection", "args": CONFIG},
                {
                    "function": "update_jira_connection",
                    "args": CONFIG | {"base_url": "https://other.example.com"},
                },
            ],
        )
    assert (
        db.exec(
            select(AdminChangeSet).where(
                AdminChangeSet.org_id == principal(db, demo).org_id
            )
        ).all()
        == []
    )


@pytest.mark.parametrize("rebind", [False, True])
def test_credential_undo(db: Session, demo: tuple, rebind: bool) -> None:
    setup(db, demo)
    p = principal(db, demo)
    row = draft(
        db,
        demo,
        {"base_url": "https://new.example.com"}
        if rebind
        else {"sync_schedule": "monthly"},
    )
    c.save_secret(db, p, row.id, "secret", CANARY)
    assert c.apply(db, p, row.id, row.revision).status == "applied"
    reverse = c.undo(db, p, row.id)
    if rebind:
        assert reverse.status == "draft"
        payload = messages.messages_for(db, store.get_session(db, p, row.session_id))[
            -1
        ].payload
        assert payload["messages"][1]["type"] == "secret_input"
        assert "重新填写" in reverse.items[0].impact.text
        with pytest.raises(c.ChangeSetError, match="重新填写凭据"):
            c.apply(db, p, reverse.id, reverse.revision)
        db.rollback()
        c.save_secret(db, p, reverse.id, "secret", "restored-synthetic-secret")
        assert c.apply(db, p, reverse.id, reverse.revision).status == "applied"
    else:
        assert reverse.status == "applied"
    saved = service.read_jira(db, p.org_id)
    assert (
        saved.config["base_url"] == CONFIG["base_url"] and saved.sync_schedule == "off"
    )
    assert decrypt_secret(saved.secret_ciphertext) == (
        "restored-synthetic-secret" if rebind else CANARY
    )
    assert CANARY not in json.dumps(c.get(db, p, row.id).items)


@pytest.mark.parametrize("outcome", ["ok", "error", "exception"])
def test_draft_connection_is_private_and_does_not_save(
    db: Session,
    demo: tuple,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    setup(db, demo)
    p = principal(db, demo)
    row = draft(db, demo, {"base_url": "https://new.example.com"})
    url = f"{demo[0]}/admin-assistant/change-sets/{row.id}"
    headers = demo[2]["admin"]
    calls = []

    def probe(config: object, secret: str) -> object:
        calls.append((config.base_url, secret))
        if outcome == "exception":
            raise RuntimeError(CANARY)
        if outcome == "error":
            raise SourceAuthError(CANARY)
        return service.JiraConnectionTest(ok=True, message=CANARY, account_name=CANARY)

    monkeypatch.setattr(service, "test_jira", probe)
    missing = client.post(url + "/test", headers=headers, json={"item": 0})
    assert (
        missing.status_code == 200 and "请先填写新的凭据" in missing.text and not calls
    )
    c.save_secret(db, p, row.id, "secret", CANARY)
    db.refresh(row)
    ciphertext, revision = row.secrets, row.revision
    before = service.read_jira(db, p.org_id).model_dump(mode="json")
    result = client.post(url + "/test", headers=headers, json={"item": 0})
    assert result.status_code == 200 and result.json()["ok"] == (outcome == "ok")
    assert calls == [("https://new.example.com", CANARY)] and CANARY not in result.text
    db.expire_all()
    assert (
        row.secrets == ciphertext and row.revision == revision and row.status == "draft"
    )
    assert service.read_jira(db, p.org_id).model_dump(mode="json") == before
    for auth in ("foreign", "employee", "manager"):
        assert (
            client.post(
                url + "/test", headers=demo[2][auth], json={"item": 0}
            ).status_code
            == 404
        )
    assert (
        client.post(url + "/test", headers=headers, json={"item": 1}).status_code == 422
    )
    assert CANARY not in str(
        messages.messages_for(db, store.get_session(db, p, row.session_id))
    )
    assert CANARY not in client.get(url, headers=headers).text
    enable(db, p.org_id)

    def reply(_config: object, **kwargs: object) -> TurnResult:
        assert CANARY not in str(kwargs)
        return TurnResult(
            text="请核对连接测试结果", stop_reason="stop", model_calls=1, tool_calls=0
        )

    monkeypatch.setattr(core.pi_client, "run_turn", reply)
    core.turn(
        db, p, row.session_id, AdminTurnInput(text="查看连接测试结果", page="/admin")
    )
    assert CANARY not in str(
        db.exec(select(AuditEvent).where(AuditEvent.org_id == p.org_id)).all()
    )
    c.discard(db, p, row.id)
    assert (
        client.post(url + "/test", headers=headers, json={"item": 0}).status_code == 409
    )


def test_saved_credentials_and_applied_test_rejection(
    db: Session, demo: tuple, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup(db, demo)
    p = principal(db, demo)
    row = draft(db, demo, {"sync_schedule": "monthly"})
    received = []
    monkeypatch.setattr(
        service,
        "test_jira",
        lambda config, secret: (
            received.append(secret)
            or service.JiraConnectionTest(ok=True, message="成功")
        ),
    )
    url = f"{demo[0]}/admin-assistant/change-sets/{row.id}/test"
    assert client.post(url, headers=demo[2]["admin"], json={"item": 0}).json()["ok"]
    assert received == ["old-synthetic-secret"]
    assert c.apply(db, p, row.id, row.revision).status == "applied"
    assert (
        client.post(url, headers=demo[2]["admin"], json={"item": 0}).status_code == 409
    )


def test_slow_connection_does_not_block_other_requests(
    db: Session, demo: tuple, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup(db, demo)
    row = draft(db, demo, {})
    entered, release = Event(), Event()

    def probe(_config: object, _secret: str) -> service.JiraConnectionTest:
        entered.set()
        assert release.wait(5)
        return service.JiraConnectionTest(ok=True, message="成功")

    monkeypatch.setattr(service, "test_jira", probe)
    url = f"{demo[0]}/admin-assistant/change-sets/{row.id}"
    headers = demo[2]["admin"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        tested = pool.submit(
            client.post, url + "/test", headers=headers, json={"item": 0}
        )
        try:
            assert entered.wait(5)
            read = pool.submit(client.get, url, headers=headers)
            assert read.result(timeout=2).status_code == 200
        finally:
            release.set()
        assert tested.result(timeout=5).json()["ok"]


@pytest.mark.parametrize(
    "name", ["start_jira_sync", "retry_sync_run", "link_identity", "unlink_identity"]
)
@pytest.mark.parametrize("mode", ["assistant", "manual", "rollback"])
def test_source_actions_and_atomic_rollback(
    db: Session, demo: tuple, client: TestClient, name: str, mode: str
) -> None:
    identity = setup(db, demo)
    rollback = mode == "rollback"
    p = principal(db, demo)
    args = {}
    run = None
    if name == "retry_sync_run":
        run = SyncRun(
            org_id=p.org_id,
            connection_id=service.read_jira(db, p.org_id).id,
            requested_by_user_id=p.user_id,
            status="failed",
            error="synthetic failure",
        )
        db.add(run)
        db.commit()
        args = {"run_id": str(run.id)}
    if name in ("link_identity", "unlink_identity"):
        args = {"identity_id": str(identity.id)}
        if name == "link_identity":
            args["employee_id"] = str(demo[1]["E02"].id)
        else:
            identity.employee_id = demo[1]["E02"].id
            db.commit()
    previous = identity.employee_id

    def jobs() -> int:
        return db.execute(
            text(
                "SELECT count(*) FROM procrastinate_jobs WHERE task_name='sources.sync_run' AND args->>'org_id'=:org"
            ),
            {"org": str(p.org_id)},
        ).scalar_one()

    try:
        before_jobs = jobs()
        items = [{"function": name, "args": args}]
        if rollback:
            items.append(
                {"function": "complete_setup_step", "args": {"key": "missing"}}
            )
        if mode == "manual":
            method, path = registry.FUNCTIONS[name].endpoints[0]
            response = client.request(
                method,
                demo[0] + path.format(**args),
                headers=demo[2]["admin"],
                json={"links": [args]} if name == "link_identity" else None,
            )
            assert response.status_code in (200, 202), response.text
        else:
            row = proposal(db, demo, items=items)
        if rollback:
            with pytest.raises(c.ChangeSetError, match="均已回滚"):
                c.apply(db, p, row.id, row.revision)
            assert jobs() == before_jobs
        elif mode == "assistant":
            assert c.apply(db, p, row.id, row.revision).status == "applied"
        db.expire_all()
        if name in ("link_identity", "unlink_identity"):
            assert identity.employee_id == (
                previous
                if rollback
                else demo[1]["E02"].id
                if name == "link_identity"
                else None
            )
            if mode == "assistant":
                assert c.undo(db, p, row.id).status == "applied"
                assert identity.employee_id == previous
        elif name == "retry_sync_run":
            assert run.status == ("failed" if rollback else "queued")
        else:
            assert len(
                db.exec(select(SyncRun).where(SyncRun.org_id == p.org_id)).all()
            ) == (0 if rollback else 1)
        if not rollback and name in ("start_jira_sync", "retry_sync_run"):
            assert jobs() == before_jobs + 1
        if name in ("start_jira_sync", "retry_sync_run"):
            events = db.exec(
                select(AuditEvent).where(
                    AuditEvent.org_id == p.org_id,
                    AuditEvent.action == "sync_run.queued",
                )
            ).all()
            assert len(events) == (0 if rollback else 1)
            if events:
                assert (events[0].details.get("via") == "admin_assistant") == (
                    mode == "assistant"
                )
    finally:
        db.rollback()
        db.execute(
            text(
                "DELETE FROM procrastinate_jobs WHERE task_name='sources.sync_run' AND args->>'org_id'=:org"
            ),
            {"org": str(p.org_id)},
        )
        db.commit()
