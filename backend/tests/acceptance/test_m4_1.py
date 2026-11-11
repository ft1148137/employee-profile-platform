"""The deterministic subset executes the same task files as live evaluation."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from sqlmodel import Session, select

from app.authz.deps import get_org_principal
from app.core.db import engine
from app.domains.admin_assistant import core
from app.domains.admin_assistant.models import AdminChangeSet
from app.domains.audit.models import ActorKind, AuditEvent
from app.domains.bot.admission import BotDraftingPriority
from app.domains.change_request.models import ProfileChangeRequest
from app.domains.drafting.update_settings import (
    UpdateSettingsInput,
    save_update_settings,
)
from app.domains.model import pi_client
from app.domains.model.client import ModelRateLimited, ModelUnavailable
from app.domains.org.models import Employee, Organization
from app.models import User
from scripts import eval_admin_assistant as runner
from scripts.eval_admin_assistant import Run, Task, tasks


@pytest.mark.parametrize(
    "task", [t for t in tasks() if t.fake_model is not None], ids=lambda t: t.id
)
def test_admin_acceptance(task: Task, db: Session) -> None:
    run = Run(task, fake=True)
    result = run.execute()
    assert result.status == "passed", result.model_dump()
    assert (result.unconfirmed, result.unauthorized, result.leaks) == (0, 0, 0)
    if task.id == "text-confirm":
        assert result.parameter_errors == 1
    if task.id == "injection-name":
        card = next(
            c["change_set"]
            for c in reversed(run.cards["first"])
            if c["type"] == "admin_change_set"
        )
        assert card["items"][0]["args"]["employee_ids"] == [run.context["target"]]
    db.expire_all()
    assert len(result.created_org_ids) == 2
    assert not db.exec(
        select(Organization).where(Organization.id.in_(result.created_org_ids))
    ).all()


@pytest.mark.parametrize(
    "fault",
    ["unconfirmed", "unauthorized", "leaks", "foreign_draft", "items", "colleague"],
)
def test_runner_detects_hard_failure(
    fault: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Run.wrap_turn
    check_secrets = Run.check_secrets

    def inject_items(self: Run, db: Session, org_id: Any) -> None:
        row = db.exec(
            select(AdminChangeSet).where(AdminChangeSet.org_id == org_id)
        ).first()
        assert row
        row.items = [{**row.items[0], "injected": self.canary}]
        db.add(row)
        db.commit()
        check_secrets(self, db, org_id)

    def inject(self: Run, *args: object, **kwargs: object) -> pi_client.TurnResult:
        result = original(self, *args, **kwargs)
        if fault == "colleague":
            self.outputs.append({"data": self.colleague_private})
        elif fault == "foreign_draft":
            with Session(engine) as db:
                outsider = db.get(Employee, self.context["foreign_employee"])
                db.add(
                    ProfileChangeRequest(
                        org_id=outsider.org_id,
                        subject_employee_id=outsider.id,
                        origin="manual",
                        status="draft",
                        author={},
                    )
                )
                db.commit()
        elif fault in ("leaks", "unauthorized"):
            result.session_state["messages"].append(
                {
                    "role": "assistant",
                    "content": self.canary if fault == "leaks" else self.private,
                }
            )
        return result

    # Streaming retains message foreign-key locks until the turn commits.
    if fault == "unconfirmed":
        original_turn = core.turn

        def unconfirmed(*args: Any, **kwargs: Any) -> Any:
            result = original_turn(*args, **kwargs)
            with Session(engine) as db:
                org = db.get(Organization, args[1].org_id)
                user = db.exec(
                    select(User).where(User.username == f"{org.slug}@example.com")
                ).one()
                save_update_settings(
                    db,
                    get_org_principal(org.id, user, db),
                    UpdateSettingsInput(reminder_schedule="yearly"),
                )
            return result

        monkeypatch.setattr(core, "turn", unconfirmed)

    monkeypatch.setattr(Run, "wrap_turn", inject)
    if fault == "items":
        monkeypatch.setattr(Run, "check_secrets", inject_items)
    task_id = (
        "employee-submit" if fault in ("foreign_draft", "colleague") else "text-confirm"
    )
    result = Run(next(t for t in tasks() if t.id == task_id), True).execute()
    assert result.status == "failed"
    metric = {
        "foreign_draft": "unauthorized",
        "colleague": "unauthorized",
        "items": "leaks",
    }.get(fault, fault)
    assert getattr(result, metric) == 1


def test_cleanup_accepts_already_deleted_user(monkeypatch: pytest.MonkeyPatch) -> None:
    run = Run(next(t for t in tasks() if t.id == "text-confirm"), True)
    original = runner.enterprise

    @contextmanager
    def remove_users(*args: Any, **kwargs: Any) -> Iterator[Any]:
        with original(*args, **kwargs) as data:
            yield data
        with Session(engine) as db:
            for role in ("admin", "employee", "manager"):
                user = db.get(User, run.context[role + "_user"])
                assert user
                db.delete(user)
            db.commit()

    monkeypatch.setattr(runner, "enterprise", remove_users)
    assert run.execute().status == "passed"


@pytest.mark.parametrize("missing_turn", [None, 1, 2])
def test_live_turn_requires_request_capture(
    missing_turn: int | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Run.wrap_turn
    send = httpx.Client.send

    def transport(
        client: httpx.Client, request: httpx.Request, **kwargs: Any
    ) -> httpx.Response:
        if request.url.host == "synthetic.example.com":
            return httpx.Response(200)
        return send(client, request, **kwargs)

    monkeypatch.setattr(httpx.Client, "send", transport)
    monkeypatch.setattr(pi_client.settings, "PI_URL", "http://unused.example.com/")

    def invoke(
        self: Run, _original: Any, config: pi_client.PiModelConfig, **kwargs: Any
    ) -> pi_client.TurnResult:
        def uncaptured(*_args: Any, **_kwargs: Any) -> pi_client.TurnResult:
            if self.result.turns != missing_turn:
                with httpx.Client() as client:
                    client.post(
                        "https://synthetic.example.com/prefix/v1/turns",
                        json={"input": "synthetic"},
                    )
            return pi_client.TurnResult(
                text="合成回复", stop_reason="stop", model_calls=1, tool_calls=0
            )

        with monkeypatch.context() as local:
            local.setattr(self, "fake", False)
            return original(self, uncaptured, config, **kwargs)

    monkeypatch.setattr(Run, "wrap_turn", invoke)
    task = next(t for t in tasks() if t.id == "text-confirm").model_copy(
        update={"fake_model": []}
    )
    result = Run(task, True).execute()
    assert result.status == ("passed" if missing_turn is None else "failed")
    assert result.categories == (
        [] if missing_turn is None else ["runner_capture_missing"]
    )


def test_live_turn_without_model_calls_needs_no_capture() -> None:
    run = Run(next(t for t in tasks() if t.id == "text-confirm"), False)
    config = pi_client.PiModelConfig(
        provider="synthetic",
        api="openai-responses",
        id="synthetic",
        baseUrl="https://example.com",
    )
    run.wrap_turn(
        lambda *_args, **_kwargs: pi_client.TurnResult(
            text="", stop_reason="stop", model_calls=0, tool_calls=0
        ),
        config,
        on_tool_call=lambda *_: {},
    )
    assert run.result.status == "passed"
    assert run.result.categories == []


def test_fake_request_keys_match_pi_client(monkeypatch: pytest.MonkeyPatch) -> None:
    run = Run(next(t for t in tasks() if t.id == "text-confirm"), True)
    config = pi_client.PiModelConfig(
        provider="synthetic",
        api="openai-responses",
        id="synthetic",
        baseUrl="https://example.com",
    )
    kwargs = {
        "mode": "chat",
        "session_state": None,
        "system_prompt": "synthetic",
        "input": "synthetic",
        "tools": [],
        "on_tool_call": lambda *_: {},
    }
    run.wrap_turn(None, config, **kwargs)
    actual = []

    def respond(request: httpx.Request) -> httpx.Response:
        actual.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "type": "done",
                "stopReason": "stop",
                "model_calls": 1,
                "tool_calls": 0,
                "session_state": {"version": 1, "messages": []},
            },
        )

    original_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kw: original_client(**kw, transport=httpx.MockTransport(respond)),
    )
    pi_client.run_turn(config, **kwargs)
    assert set(run.captures[0]) == set(actual[0])


@pytest.mark.parametrize("unchanged", [False, True])
def test_read_audit_is_not_unconfirmed(
    unchanged: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Run.wrap_turn

    def inject(self: Run, *args: Any, **kwargs: Any) -> pi_client.TurnResult:
        result = original(self, *args, **kwargs)
        with Session(engine) as db:
            db.add(
                AuditEvent(
                    org_id=self.context["org"],
                    actor_type=ActorKind.USER,
                    actor_id=str(self.context["admin_user"]),
                    action="synthetic.read",
                    target_type="organization",
                    details={},
                )
            )
            db.commit()
        return result

    monkeypatch.setattr(Run, "wrap_turn", inject)
    task = next(t for t in tasks() if t.id == "text-confirm").model_copy(
        update={"expect": {"unchanged": unchanged}}
    )
    result = Run(task, True).execute()
    assert result.unconfirmed == 0
    assert result.status == ("failed" if unchanged else "passed")


def test_reply_contains_fails_on_missing_text() -> None:
    task = next(t for t in tasks() if t.id == "text-confirm").model_copy(deep=True)
    task.steps[0]["expect"] = {"reply_contains": ["missing synthetic reply"]}
    assert Run(task, True).execute().status == "failed"


@pytest.mark.parametrize(
    "expected,passed",
    [
        ("未开启", True),
        ("关闭", False),
        (["飞书", "未开启"], True),
        ([["关闭", "未开启"], ["不能", "无法"]], True),
        ([["关闭", "未开启"], ["通过", "已启用"]], False),
        ([[]], False),
        ([], True),
    ],
)
def test_reply_contains_alternatives(
    expected: str | list[str | list[str]], passed: bool
) -> None:
    assert runner.reply_contains("飞书未开启，不能执行", expected) is passed


@pytest.mark.parametrize("path", [["missing"], ["data", "data", 99999]])
def test_capture_requires_existing_path(path: list[str | int]) -> None:
    task = next(t for t in tasks() if t.id == "injection-name").model_copy(deep=True)
    task.steps[0]["capture"]["target"]["path"] = path
    assert Run(task, True).execute().status == "failed"


def test_snapshots_once_per_step() -> None:
    task = next(t for t in tasks() if t.id == "text-confirm")
    run = Run(task, True)
    with patch.object(run, "snapshot", wraps=run.snapshot) as snapshot:
        assert run.execute().status == "passed"
        assert snapshot.call_count == len(task.steps) + 1


def test_report_counts_each_menu(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    report = tmp_path / "report.md"
    monkeypatch.setattr("sys.argv", ["eval", "--fake", "--report", str(report)])
    monkeypatch.setattr(Run, "execute", lambda self, *_: runner.Result(id=self.task.id))
    with pytest.raises(SystemExit) as error:
        runner.main()
    assert error.value.code == 0
    text = report.read_text()
    for menu in ("home", "people", "rules", "data_ai", "connections"):
        selected = [t for t in tasks() if t.fake_model is not None]
        assert f"| {menu} | {sum(t.menu == menu for t in selected)} |" in text


def test_employee_may_read_own_restricted_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = Run.wrap_turn

    def inject(self: Run, *args: object, **kwargs: object) -> pi_client.TurnResult:
        result = original(self, *args, **kwargs)
        result.text = self.private
        return result

    monkeypatch.setattr(Run, "wrap_turn", inject)
    result = Run(next(t for t in tasks() if t.id == "employee-submit"), True).execute()
    assert result.status == "passed", result
    assert result.unauthorized == 0


@pytest.mark.parametrize(
    "error,category,status",
    [
        (ModelRateLimited(None), "subscription_rate_limit:", "deferred"),
        (BotDraftingPriority(), "drafting_paused", "deferred"),
        (pi_client.PiSessionInvalid(), "session_invalid", "failed"),
        (ModelUnavailable(), "model_unavailable", "failed"),
    ],
)
def test_model_errors_are_distinct(
    error: Exception, category: str, status: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Run.wrap_turn

    def fail(*_args: object, **_kwargs: object) -> None:
        raise error

    def invoke(
        self: Run, _original: object, config: pi_client.PiModelConfig, **kwargs: object
    ) -> pi_client.TurnResult:
        with monkeypatch.context() as local:
            local.setattr(self, "fake", False)
            return original(self, fail, config, **kwargs)

    monkeypatch.setattr(Run, "wrap_turn", invoke)
    result = Run(tasks()[0], True).execute()
    assert result.status == status, result
    assert any(c.startswith(category) for c in result.categories)
    assert (result.unconfirmed, result.unauthorized, result.leaks) == (0, 0, 0)
