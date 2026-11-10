"""The deterministic subset executes the same task files as live evaluation."""

import pytest
from sqlmodel import Session, select

from app.authz.deps import get_org_principal
from app.core.db import engine
from app.domains.bot.admission import BotDraftingPriority
from app.domains.drafting.update_settings import (
    UpdateSettingsInput,
    save_update_settings,
)
from app.domains.model import pi_client
from app.domains.model.client import ModelRateLimited, ModelUnavailable
from app.domains.org.models import Organization
from app.models import User
from scripts.eval_admin_assistant import Run, Task, tasks


@pytest.mark.parametrize(
    "task", [t for t in tasks() if t.fake_model is not None], ids=lambda t: t.id
)
def test_admin_acceptance(task: Task, db: Session) -> None:
    before = set(db.exec(select(Organization.id)).all())
    result = Run(task, fake=True).execute()
    assert result.status == "passed", result.model_dump()
    assert (result.unconfirmed, result.unauthorized, result.leaks) == (0, 0, 0)
    if task.id == "text-confirm":
        assert result.parameter_errors == 1
    db.expire_all()
    assert set(db.exec(select(Organization.id)).all()) == before


@pytest.mark.parametrize("fault", ["unconfirmed", "unauthorized", "leaks"])
def test_runner_detects_hard_failure(
    fault: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Run.wrap_turn

    def inject(self: Run, *args: object, **kwargs: object) -> pi_client.TurnResult:
        result = original(self, *args, **kwargs)
        if fault == "unconfirmed":
            with Session(engine) as db:
                org = db.get(Organization, self.context["org"])
                user = db.exec(
                    select(User).where(User.username == f"{org.slug}@example.com")
                ).one()
                save_update_settings(
                    db,
                    get_org_principal(org.id, user, db),
                    UpdateSettingsInput(reminder_schedule="yearly"),
                )
        else:
            result.session_state["messages"].append(
                {
                    "role": "assistant",
                    "content": self.canary if fault == "leaks" else self.private,
                }
            )
        return result

    monkeypatch.setattr(Run, "wrap_turn", inject)
    result = Run(next(t for t in tasks() if t.id == "text-confirm"), True).execute()
    assert result.status == "failed"
    assert getattr(result, fault) == 1


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
