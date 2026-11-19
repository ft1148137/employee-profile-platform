from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.domains.bot import tools
from app.domains.capability.service import get_schema, list_capabilities
from app.domains.im.rendering import render
from app.domains.review.publish import current_claims
from tests.api.routes.test_approval_policy import save
from tests.api.routes.test_reviews import actor


@pytest.mark.parametrize(
    "scenario,requires_manager,partial_auto_publish,expected",
    [
        ("default", True, False, "提交后由 {reviewers}审批"),
        ("exempt", False, False, "本能力无需经理确认，提交后将直接发布为暂定"),
        (
            "mixed",
            True,
            True,
            "部分能力无需经理确认（提交后将直接发布为暂定），其余提交后由 {reviewers}审批",
        ),
        ("empty", True, False, "提交后由 {reviewers}审批"),
    ],
)
def test_preview_policy_matches_bot_and_im(
    demo: tuple,
    db: Session,
    client: TestClient,
    scenario: str,
    requires_manager: bool,
    partial_auto_publish: bool,
    expected: str,
) -> None:
    url, employees, auth = demo
    org_id = employees["E02"].org_id
    definitions = list_capabilities(db, org_id, get_schema(db, org_id).id)
    backend = next(c for c in definitions if c.key == "backend_api")
    assert backend.category
    if scenario in ("exempt", "mixed", "empty"):
        save(client, demo, category_overrides={backend.category: False})
    owner = actor(db, org_id, "employee.platform@demo.example.com")
    current = current_claims(db, org_id, owner.employee_id)
    keys = [] if scenario == "empty" else [backend.key]
    if scenario == "mixed":
        keys.append(next(c.key for c in definitions if c.category != backend.category))
    draft = client.post(
        f"{url}/me/requests",
        headers=auth["employee"],
        json={
            "items": [
                {
                    "op": "modify" if key in current else "add",
                    "capability_key": key,
                    "statement": "合成审批预览",
                }
                for key in keys
            ]
        },
    )
    assert draft.status_code == 201, draft.text
    request_id = draft.json()["id"]
    response = client.get(
        f"{url}/me/requests/{request_id}/preview", headers=auth["employee"]
    )
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["requires_manager"] is requires_manager
    assert preview["partial_auto_publish"] is partial_auto_publish

    card = tools.preview_card(db, owner, UUID(request_id))
    assert card.requires_manager is requires_manager
    assert card.partial_auto_publish is partial_auto_publish
    reviewers = "、".join(f"{r['name']}（{r['role']}）" for r in preview["reviewers"])
    assert reviewers
    assert render(card)[0].splitlines()[-1] == expected.format(reviewers=reviewers)
    if partial_auto_publish:
        assert (
            render(card.model_copy(update={"reviewers": []}))[0].splitlines()[-1]
            == "部分能力无需经理确认（提交后将直接发布为暂定），其余暂时没有可用的审批人，企业管理员会处理"
        )
