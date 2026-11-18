from typing import Any, Literal
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.domains.bot import core, store
from app.domains.bot.schemas import ProjectDraftCard, TurnResponse
from app.domains.capacity.models import CapacityDemand, CapacityProject
from app.domains.im.rendering import render
from app.domains.model.pi_client import TurnResult
from tests.api.routes.test_m10_project import project_data as project_data
from tests.domains.test_bot_core import enable
from tests.domains.test_im_gateway import principal

BOT = "/api/v1/bot/sessions"


@pytest.mark.parametrize("channel", ["web", "feishu"])
def test_project_draft_requires_web_confirmation(
    channel: Literal["web", "feishu"],
    demo: tuple,
    project_data: tuple,
    db: Session,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, people, _ = demo
    capacity, auth, body, _ = project_data
    owner = principal(db, people["E01"].org_id, people["E01"].id)
    enable(db, owner.org_id)
    session = store.create_session(db, owner, channel)
    org_query = {"org_id": str(owner.org_id)}
    results: list[dict] = []

    def model(*_args: Any, **kwargs: Any) -> TurnResult:
        call = kwargs["on_tool_call"]
        result = call(
            "coordination", {"function": "prepare_project_draft", "arguments": body}
        )
        results.append(result)
        return TurnResult(
            text="用户已确认", model_calls=2, tool_calls=1, stop_reason="stop"
        )

    def turn() -> TurnResponse:
        return core.turn(db, owner, session.id, "同意，生成项目草稿")

    monkeypatch.setattr(core.pi_client, "run_turn", model)
    first_role = body["roles"][0]
    for field, invalid, hint in (
        ("target_department_id", str(uuid4()), "目标部门"),
        ("capability_key", "unknown-capability", "能力词典"),
        (
            "description",
            f"{people['E02'].display_name} 的个人负荷为 90%",
            "只写项目工作",
        ),
        ("description", people["E01"].display_name, "只写项目工作"),
        ("role_name", f"角色{people['E02'].id}", "只写项目工作"),
    ):
        previous = first_role[field]
        first_role[field] = invalid
        invalid_response = turn()
        assert not any(
            isinstance(m, ProjectDraftCard) for m in invalid_response.messages
        )
        assert hint in invalid_response.model_dump_json()
        assert hint in results[-1]["error"]
        first_role[field] = previous

    response = turn()
    assert "用户已确认" not in response.model_dump_json()
    first = next(m for m in response.messages if isinstance(m, ProjectDraftCard))
    assert (
        db.exec(
            select(CapacityProject).where(CapacityProject.org_id == owner.org_id)
        ).all()
        == []
    )
    assert (
        db.exec(
            select(CapacityDemand).where(CapacityDemand.org_id == owner.org_id)
        ).all()
        == []
    )

    body["title"] = "合成交付项目修订"
    card = next(m for m in turn().messages if isinstance(m, ProjectDraftCard))
    text, actions = render(card)
    assert actions == []
    assert "合成目标部门0" in text and "前置角色" in text
    for person in people.values():
        assert person.display_name not in card.model_dump_json() + text
        assert str(person.id) not in card.model_dump_json() + text
    assert "个人负荷" not in text and "用户已确认" not in text

    def action(draft: ProjectDraftCard, actor: str) -> Any:
        if channel == "feishu":
            assert draft.web_link and draft.web_link in render(draft)[0]
            token = parse_qs(urlsplit(draft.web_link).query)["draft"][0]
            params = {**org_query, "draft": token}
            url = f"{BOT}/project-drafts/feishu"
            if draft is card:
                view = client.get(url, headers=auth[actor], params=params)
                assert view.status_code == (200 if actor == "manager" else 404)
                if view.is_success:
                    assert view.json()["type"] == "project_draft_card"
                    assert "用户已确认" not in view.text
            return client.post(url, headers=auth[actor], params=params)
        return client.post(
            f"{BOT}/{session.id}/actions",
            headers=auth[actor],
            params=org_query,
            json=draft.actions[0].model_dump(mode="json"),
        )

    assert action(first, "manager").status_code == 409
    assert action(card, "employee").status_code == 404
    assert action(card, "foreign").status_code == 404
    submitted = action(card, "manager")
    assert submitted.is_success, submitted.text
    result = ProjectDraftCard.model_validate(submitted.json()["messages"][0])
    assert result.project_id and not result.actions
    assert action(card, "manager").status_code == 409
    listing = client.get(f"{capacity}/projects", headers=auth["manager"])
    assert [(row["id"], row["title"]) for row in listing.json()] == [
        (str(result.project_id), body["title"])
    ]
    assert (
        len(
            db.exec(
                select(CapacityDemand).where(
                    CapacityDemand.project_id == result.project_id
                )
            ).all()
        )
        == 4
    )
