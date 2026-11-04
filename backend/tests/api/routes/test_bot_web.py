from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.domains.bot import core
from app.domains.change_request.models import ProfileChangeRequest
from app.domains.model.pi_client import TurnResult
from tests.api.routes.test_employee_requests import payload
from tests.domains.test_bot_core import enable


def test_saved_guide_is_used_on_next_turn(
    demo: tuple, db: Session, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    base, people, auth = demo
    org_id = people["E02"].org_id
    enable(db, org_id)
    query = {"org_id": str(org_id)}
    session_id = client.post(
        "/api/v1/bot/sessions", params=query, headers=auth["employee"]
    ).json()["session_id"]
    prompts: list[str] = []

    def reply(_config: object, **kwargs: object) -> TurnResult:
        prompts.append(str(kwargs["system_prompt"]))
        return TurnResult(
            text="合成回复", stop_reason="stop", model_calls=1, tool_calls=0
        )

    monkeypatch.setattr(core.pi_client, "run_turn", reply)
    url = f"/api/v1/bot/sessions/{session_id}"
    client.post(
        url + "/turns", params=query, headers=auth["employee"], json={"text": "你好"}
    )
    guide = "请询问一次具体跨部门合作经历"
    setting = {"question_guide": guide, "retention_days": 3}
    assert (
        client.put(
            base + "/bot-settings", headers=auth["employee"], json=setting
        ).status_code
        == 403
    )
    assert (
        client.put(
            base + "/bot-settings", headers=auth["admin"], json=setting
        ).status_code
        == 200
    )
    assert (
        client.post(
            url + "/turns",
            params=query,
            headers=auth["employee"],
            json={"text": "继续"},
        ).status_code
        == 200
    )
    assert guide not in prompts[0] and guide in prompts[1]
    history = client.get(
        url + "/messages", params=query, headers=auth["employee"]
    ).json()
    assert history[0]["payload"] == {"text": "你好"}
    assert history[1]["payload"]["messages"][0]["type"] == "text"


def test_manager_viewing_subordinate_still_drafts_for_self(
    demo: tuple, db: Session, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    base, people, auth = demo
    enable(db, people["E02"].org_id)
    assert (
        client.get(
            base + f"/employees/{people['E02'].id}/profile", headers=auth["manager"]
        ).status_code
        == 200
    )
    captured: list[UUID] = []

    def reply(_config: object, **kwargs: object) -> TurnResult:
        result = kwargs["on_tool_call"]("draft_update_request", {"items": [payload()]})
        captured.append(UUID(result["id"]))
        return TurnResult(
            text="已提交", stop_reason="stop", model_calls=1, tool_calls=1
        )

    monkeypatch.setattr(core.pi_client, "run_turn", reply)
    query = {"org_id": str(people["E02"].org_id)}
    session_id = client.post(
        "/api/v1/bot/sessions", params=query, headers=auth["manager"]
    ).json()["session_id"]
    response = client.post(
        f"/api/v1/bot/sessions/{session_id}/turns",
        params=query,
        headers=auth["manager"],
        json={"text": "补充经历"},
    )
    assert response.status_code == 200
    request = db.get(ProfileChangeRequest, captured[0])
    assert request.subject_employee_id == people["E01"].id
    assert request.status == "draft"
    assert response.json()["messages"][1]["actions"][0]["action"] == "confirm_submit"
