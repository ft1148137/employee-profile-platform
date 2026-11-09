from collections.abc import Generator
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session, select

from app.domains.admin_assistant import change_set as c
from app.domains.admin_assistant import registry
from app.domains.audit.models import AuditEvent
from app.domains.base import utc_now
from app.domains.capability.models import CapabilitySchemaVersion
from app.domains.capability.service import get_schema, read_editor
from app.domains.change_request.models import ProfileChangeRequest
from app.domains.drafting.service import read_progress
from app.domains.org.models import Organization
from app.domains.setup.models import SetupStepState
from tests.admin_assistant.test_change_set import proposal
from tests.admin_assistant.test_registry import principal
from tests.domains.test_bot_core import enable


@pytest.fixture(autouse=True)
def clean_drafting_jobs(db: Session, demo: tuple) -> Generator[None]:
    try:
        yield
    finally:
        db.rollback()
        db.execute(
            text(
                "DELETE FROM procrastinate_jobs WHERE task_name = 'profiles.draft_initial' AND args->>'org_id' = :org"
            ),
            {"org": str(demo[1]["E02"].org_id)},
        )
        db.commit()


def args_for(db: Session, demo: tuple, name: str) -> dict[str, Any]:
    p = principal(db, demo)
    if name == "update_capability_dictionary":
        editor = read_editor(db, p)
        definitions = [d.model_dump(mode="json") for d in editor.definitions]
        definitions[0]["name"] = "合成修改能力"
        return {"definitions": definitions, "expected_version": editor.expected_version}
    if name == "apply_capability_template":
        return {"template_id": "saas-software"}
    if name == "update_drafting_settings":
        return {
            "excluded_capability_keys": [],
            "disabled_department_ids": [str(demo[1]["E02"].department_id)],
        }
    if name == "start_initial_drafting":
        enable(db, p.org_id)
        return {"scope": "all"}
    if name == "hand_over_initial_profiles":
        row = db.exec(
            select(ProfileChangeRequest).where(
                ProfileChangeRequest.org_id == p.org_id,
                ProfileChangeRequest.subject_employee_id == demo[1]["E02"].id,
            )
        ).one()
        row.created_at = utc_now() - timedelta(days=15)
        db.commit()
        return {}
    if name.endswith("setup_step"):
        return {"key": "capabilities" if name.startswith("complete") else "sources"}
    return {"name": "合成企业新名称"}


ACTIONS = [
    "update_capability_dictionary",
    "apply_capability_template",
    "update_drafting_settings",
    "start_initial_drafting",
    "hand_over_initial_profiles",
    "complete_setup_step",
    "skip_setup_step",
    "update_org_profile",
]


@pytest.mark.parametrize("name", ACTIONS)
@pytest.mark.parametrize("assistant", [False, True])
def test_manual_and_assistant_results(
    db: Session, demo: tuple, client: TestClient, name: str, assistant: bool
) -> None:
    p = principal(db, demo)
    args = args_for(db, demo, name)
    before = {
        a.id
        for a in db.exec(select(AuditEvent).where(AuditEvent.org_id == p.org_id)).all()
    }
    if assistant:
        row = proposal(db, demo, items=[{"function": name, "args": args}])
        if name in (
            "update_drafting_settings",
            "start_initial_drafting",
            "hand_over_initial_profiles",
        ):
            assert row.items[0]["impact"]["count"] > 0
        assert c.apply(db, p, row.id, row.revision).status == "applied"
    else:
        method, path = registry.FUNCTIONS[name].endpoints[-1]
        response = client.request(
            method, demo[0] + path.format(**args), headers=demo[2]["admin"], json=args
        )
        assert response.status_code in (200, 202), response.text
    db.expire_all()
    if name == "update_capability_dictionary":
        assert [
            d.model_dump(mode="json") for d in read_editor(db, p).definitions
        ] == args["definitions"]
    elif name == "apply_capability_template":
        assert get_schema(db, p.org_id).template_ref.startswith(
            args["template_id"] + "@"
        )
    elif name == "update_drafting_settings":
        assert (
            str(
                registry.drafting.read_settings(db, p.org_id).disabled_department_ids[0]
            )
            == args["disabled_department_ids"][0]
        )
    elif name == "start_initial_drafting":
        assert jobs(db, p.org_id) == 1
        assert read_progress(db, p.org_id).counts["pending"] > 0
    elif name == "hand_over_initial_profiles":
        request = db.exec(
            select(ProfileChangeRequest).where(
                ProfileChangeRequest.org_id == p.org_id,
                ProfileChangeRequest.subject_employee_id == demo[1]["E02"].id,
            )
        ).one()
        assert (
            request.status == "pending_review"
            and request.submitted_via == "admin_handover"
        )
    elif name.endswith("setup_step"):
        assert db.get(SetupStepState, (p.org_id, args["key"])).status == (
            "completed" if name.startswith("complete") else "skipped"
        )
    else:
        assert db.get(Organization, p.org_id).name == args["name"]
    events = [
        a
        for a in db.exec(select(AuditEvent).where(AuditEvent.org_id == p.org_id)).all()
        if a.id not in before
    ]
    assert events or (not assistant and name == "start_initial_drafting")
    for event in events:
        if assistant:
            assert event.details["via"] == "admin_assistant"
            assert event.details["assistant_session_id"] == str(row.session_id)
            assert event.details["change_set_id"] == str(row.id)
        else:
            assert (
                "assistant_session_id" not in event.details
                and "change_set_id" not in event.details
            )
            if name == "hand_over_initial_profiles":
                assert event.details["via"] == "admin_handover"
            else:
                assert "via" not in event.details
    if (
        name
        in (
            "start_initial_drafting",
            "hand_over_initial_profiles",
            "complete_setup_step",
            "skip_setup_step",
        )
        and assistant
    ):
        with pytest.raises(c.ChangeSetError, match="不能撤销"):
            c.undo(db, p, row.id)


def jobs(db: Session, org_id: UUID) -> int:
    return db.execute(
        text("SELECT count(*) FROM procrastinate_jobs WHERE args->>'org_id'=:org"),
        {"org": str(org_id)},
    ).scalar_one()


@pytest.mark.parametrize(
    "name",
    [
        "start_initial_drafting",
        "update_capability_dictionary",
        "hand_over_initial_profiles",
        "apply_capability_template",
        "update_drafting_settings",
        "complete_setup_step",
        "skip_setup_step",
        "update_org_profile",
    ],
)
def test_later_failure_rolls_back(db: Session, demo: tuple, name: str) -> None:
    p = principal(db, demo)
    args = args_for(db, demo, name)
    if name == "update_capability_dictionary":
        enable(db, p.org_id)
        args["redraft"] = True
    models = (
        CapabilitySchemaVersion,
        ProfileChangeRequest,
        Organization,
        SetupStepState,
        AuditEvent,
    )

    def state() -> list:
        return [
            [
                r.model_dump(mode="json")
                for r in db.exec(
                    select(model).where(model.org_id == p.org_id)
                    if model is not Organization
                    else select(model).where(model.id == p.org_id)
                ).all()
            ]
            for model in models
        ] + [
            read_progress(db, p.org_id).model_dump(),
            registry.drafting.read_settings(db, p.org_id).model_dump(),
            jobs(db, p.org_id),
        ]

    before = state()
    row = proposal(
        db,
        demo,
        items=[
            {"function": name, "args": args},
            {"function": "complete_setup_step", "args": {"key": "missing"}},
        ],
    )
    with pytest.raises(c.ChangeSetError, match="均已回滚"):
        c.apply(db, p, row.id, row.revision)
    assert state() == before
    assert c.get(db, p, row.id).status == "draft" and "audit_context" not in db.info


@pytest.mark.parametrize(
    "name",
    [
        "update_capability_dictionary",
        "apply_capability_template",
        "update_drafting_settings",
        "update_org_profile",
    ],
)
def test_undo_and_stale(db: Session, demo: tuple, name: str) -> None:
    p = principal(db, demo)
    entry = registry.FUNCTIONS[name]
    args = args_for(db, demo, name)
    before = entry.snapshot(db, p, None).model_dump(mode="json")
    row = proposal(db, demo, items=[{"function": name, "args": args}])
    c.apply(db, p, row.id, row.revision)
    reverse = c.undo(db, p, row.id)
    assert reverse.status == "applied" and c.get(db, p, row.id).status == "undone"
    restored = entry.snapshot(db, p, None).model_dump(mode="json")
    if entry.reverse:
        assert restored["expected_version"] == before["expected_version"] + 2
        assert restored["definitions"] == before["definitions"]
        assert (
            len(
                db.exec(
                    select(CapabilitySchemaVersion).where(
                        CapabilitySchemaVersion.org_id == p.org_id
                    )
                ).all()
            )
            == 3
        )
        args = args_for(db, demo, name)
    else:
        assert restored == before
    stale = proposal(db, demo, items=[{"function": name, "args": args}])
    entry.handler(db, p, entry.input_model.model_validate(args))
    with pytest.raises(c.ChangeSetError, match="内容已变化"):
        c.apply(db, p, stale.id, stale.revision)
    assert c.get(db, p, stale.id).status == "stale"
