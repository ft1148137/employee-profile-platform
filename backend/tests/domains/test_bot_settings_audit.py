import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.domains.admin_assistant import change_set
from app.domains.audit.models import AuditEvent
from app.domains.bot.settings import BotSettings
from tests.admin_assistant.test_change_set import proposal
from tests.admin_assistant.test_registry import principal


@pytest.mark.parametrize("via_assistant", [False, True])
def test_question_guide_audit_snapshots(
    demo: tuple, db: Session, client: TestClient, via_assistant: bool
) -> None:
    p = principal(db, demo)
    endpoint = demo[0] + "/bot-settings"

    def events() -> list[AuditEvent]:
        return list(
            db.exec(
                select(AuditEvent).where(
                    AuditEvent.org_id == p.org_id,
                    AuditEvent.action == "bot_settings.updated",
                )
            ).all()
        )

    before = BotSettings().model_dump(mode="json")
    # Saving the effective defaults should not create a change event.
    assert (
        client.put(endpoint, headers=demo[2]["admin"], json=before).status_code == 200
    )
    assert not events()
    for after in (
        {"question_guide": "请询问已确认的合成项目经历", "retention_days": 7},
        {"question_guide": "请补充合成项目中的个人贡献", "retention_days": 7},
        {"question_guide": "请补充合成项目中的个人贡献", "retention_days": 14},
        {"question_guide": "", "retention_days": 14},
    ):
        previous_ids = {event.id for event in events()}
        if via_assistant:
            row = proposal(
                db, demo, items=[{"function": "update_bot_settings", "args": after}]
            )
            assert not {event.id for event in events()} - previous_ids
            assert change_set.apply(db, p, row.id, row.revision).status == "applied"
            assert row.items[0]["before"] == before
            assert row.items[0]["after"] == after
        else:
            assert (
                client.put(endpoint, headers=demo[2]["admin"], json=after).json()
                == after
            )
        added = [event for event in events() if event.id not in previous_ids]
        assert len(added) == 1
        event = added[0]
        assert event.details["before"] == before
        assert event.details["after"] == after
        assert event.actor_id == str(p.user_id)
        assert event.target_id == str(p.org_id)
        if via_assistant:
            assert event.details["via"] == "admin_assistant"
            assert event.details["change_set_id"] == str(row.id)
        saved_ids = {event.id for event in events()}
        assert (
            client.put(endpoint, headers=demo[2]["admin"], json=after).json() == after
        )
        assert {event.id for event in events()} == saved_ids
        before = after
