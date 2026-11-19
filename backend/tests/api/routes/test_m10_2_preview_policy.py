import asyncio
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.domains.bot import schemas as bot
from app.domains.bot import store, tools
from app.domains.capability.service import get_schema, list_capabilities
from app.domains.change_request import employee
from app.domains.change_request.models import ProfileChangeRequest
from app.domains.change_request.schemas import ItemInput
from app.domains.im import actions
from app.domains.im.models import ImAction
from app.domains.im.rendering import render
from app.domains.review.publish import current_claims
from tests.api.routes.test_approval_policy import save
from tests.api.routes.test_reviews import actor
from tests.domains.test_im_conversation import button
from tests.domains.test_im_gateway import event


@pytest.mark.parametrize(
    "scenario,requires_manager,partial_auto_publish",
    [
        ("default", True, False),
        ("exempt", False, False),
        ("retract", False, False),
        ("mixed", True, True),
        ("empty", True, False),
    ],
)
def test_preview_policy_matches_bot_and_im(
    demo: tuple,
    db: Session,
    client: TestClient,
    scenario: str,
    requires_manager: bool,
    partial_auto_publish: bool,
) -> None:
    url, employees, auth = demo
    org_id = employees["E02"].org_id
    definitions = list_capabilities(db, org_id, get_schema(db, org_id).id)
    backend = next(c for c in definitions if c.key == "backend_api")
    assert backend.category
    owner = actor(db, org_id, "employee.platform@demo.example.com")
    pending_notice = "提交后需经理确认"
    tentative_notice = "本能力无需经理确认，提交后将直接发布为暂定"
    retract_notice = "提交后立即撤回，能力状态变为未知"
    changes = []
    if scenario in ("retract", "mixed"):
        changes.append((backend.key, "retract", retract_notice))
    elif scenario != "empty":
        changes.append(
            (
                backend.key,
                "modify",
                pending_notice if requires_manager else tentative_notice,
            )
        )
    if scenario == "mixed":
        other = next(c for c in definitions if c.category != backend.category)
        save(client, demo, require_manager=False)
        seed = employee.save_draft(
            owner,
            db,
            None,
            [ItemInput(op="add", capability_key=other.key, statement="合成判断")],
            "web",
        )
        employee.submit(owner, db, seed.id, seed.revision, "web")
        db.commit()
        additional = next(
            c
            for c in definitions
            if c.key != backend.key and c.category == backend.category
        )
        changes.extend(
            [
                (other.key, "retract", pending_notice),
                (additional.key, "add", tentative_notice),
            ]
        )
    if scenario != "default":
        save(
            client,
            demo,
            require_manager=True,
            category_overrides={backend.category: False},
        )
    draft = client.post(
        f"{url}/me/requests",
        headers=auth["employee"],
        json={
            "items": [
                {
                    "op": op,
                    "capability_key": key,
                    "statement": "合成审批预览",
                }
                for key, op, _ in changes
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
    assert [
        (
            item["after"]["capability"]["key"],
            item["after"]["op"],
            item["submission_notice"],
        )
        for item in preview["items"]
    ] == changes

    card = tools.preview_card(db, owner, UUID(request_id))
    assert card.requires_manager is requires_manager
    assert card.partial_auto_publish is partial_auto_publish
    reviewers = "、".join(f"{r['name']}（{r['role']}）" for r in preview["reviewers"])
    assert reviewers
    prefix = "需经理确认的改动：" if partial_auto_publish else ""
    text, actions = render(card)
    assert actions[0]["action"] == "confirm_submit"
    blocks = text.split("\n\n") if changes else []
    for item, (_, _, notice), block in zip(card.items, changes, blocks, strict=True):
        assert item.submission_notice == notice
        assert item.after.capability.name in block
        assert set(block.splitlines()) & {
            pending_notice,
            tentative_notice,
            retract_notice,
        } == {notice}
    if requires_manager:
        assert text.splitlines()[-1] == f"{prefix}提交后由 {reviewers}审批"
    else:
        assert f"提交后由 {reviewers}审批" not in text
        assert "没有可用的审批人" not in text
    if scenario == "mixed":
        assert (
            render(card.model_copy(update={"reviewers": []}))[0].splitlines()[-1]
            == f"{prefix}暂时没有可用的审批人，企业管理员会处理"
        )
        db.commit()
        submitted = client.post(
            f"{url}/me/requests/{request_id}/submit",
            headers=auth["employee"],
            json={"revision": preview["revision"]},
        )
        assert submitted.status_code == 200, submitted.text
        assert [item["decision"] for item in submitted.json()["items"]] == [
            "approved",
            None,
            "approved",
        ]
        db.expire_all()
        published = current_claims(db, org_id, owner.employee_id)
        assert published[backend.key].assessment_status == "unknown"
        assert published[backend.key].level is None
        assert published[other.key].assessment_status == "tentative"
        assert published[additional.key].assessment_status == "tentative"


@pytest.mark.parametrize(
    "channel,version",
    [
        ("web", "legacy"),
        ("telegram", "legacy"),
        ("web", "already_notified"),
        ("web", "current"),
    ],
)
def test_assistant_confirms_immediate_retraction_after_notice(
    demo: tuple, db: Session, client: TestClient, channel: str, version: str
) -> None:
    _, people, auth = demo
    org_id = people["E02"].org_id
    owner = actor(db, org_id, "employee.platform@demo.example.com")
    definitions = list_capabilities(db, org_id, get_schema(db, org_id).id)
    backend = next(c for c in definitions if c.key == "backend_api")
    assert backend.category
    items = [ItemInput(op="retract", capability_key=backend.key, statement="申请撤回")]
    mixed = channel == "telegram"
    if mixed:
        other = next(c for c in definitions if c.category != backend.category)
        save(client, demo, require_manager=False)
        seed = employee.save_draft(
            owner,
            db,
            None,
            [ItemInput(op="add", capability_key=other.key, statement="合成判断")],
            "web",
        )
        employee.submit(owner, db, seed.id, seed.revision, "web")
        db.commit()
        items.append(
            ItemInput(op="retract", capability_key=other.key, statement="申请撤回")
        )
    save(client, demo, category_overrides={backend.category: False})
    original = current_claims(db, org_id, owner.employee_id)[backend.key]
    assert original.level is not None
    draft = employee.save_draft(owner, db, None, items, "bot")
    incoming = event(org_id, "预览撤回")
    session = store.create_session(db, owner, channel, "100", "12345")
    session_id = session.id
    request_id = draft.id
    url = f"/api/v1/bot/sessions/{session_id}"
    query = {"org_id": str(org_id)}
    card = tools.preview_card(db, owner, request_id)
    payload = card.model_dump(mode="json")
    for action in payload["actions"]:
        action.pop("preview_id")
    legacy = {**payload, "items": [dict(item) for item in payload["items"]]}
    for item in legacy["items"]:
        item.pop("submission_notice")
    store.append_message(db, session, "assistant", {"messages": [legacy], "steps": []})
    if version == "already_notified":
        # PR #405 displayed the notice with the same action payload as a legacy card.
        store.append_message(
            db, session, "assistant", {"messages": [payload], "steps": []}
        )
        card = bot.PreviewCard.model_validate(payload)
    elif version == "current":
        store.append_message(
            db,
            session,
            "assistant",
            bot.TurnResponse(messages=[card]).model_dump(mode="json"),
        )
    else:
        card = bot.PreviewCard.model_validate(legacy)
    db.commit()

    rendered: list[str] = []

    class FakeTelegram:
        async def render(self, chat: str, text: str, buttons: list) -> str:
            rendered.append(text)
            return str(len(rendered))

    def confirm(preview: bot.PreviewCard) -> bot.TurnResponse:
        if channel == "web":
            response = client.post(
                url + "/actions",
                params=query,
                headers=auth["employee"],
                json=preview.actions[0].model_dump(mode="json"),
            )
            assert response.status_code == 200, response.text
            return bot.TurnResponse.model_validate(response.json())
        asyncio.run(
            actions.deliver(
                db,
                owner,
                incoming,
                "12345",
                actions.Reply(session_id, bot.TurnResponse(messages=[preview])),
                FakeTelegram(),
            )
        )
        row = db.exec(
            select(ImAction).where(
                ImAction.org_id == org_id,
                ImAction.session_id == session_id,
                ImAction.message_id == str(len(rendered)),
                ImAction.action["action"].astext == "confirm_submit",
            )
        ).one()
        reply = actions.callback(db, owner, button(incoming, row), "12345")
        assert isinstance(reply, actions.Reply)
        return reply.response

    notice = "提交后立即撤回，能力状态变为未知"
    response = confirm(card)
    if version == "legacy":
        (fresh,) = response.messages
        assert isinstance(fresh, bot.PreviewCard)
        assert fresh.request_id == card.request_id
        assert fresh.revision == card.revision
        assert fresh.base_revision == card.base_revision
        assert fresh.actions[0].preview_id is not None
        assert [item.submission_notice for item in fresh.items] == (
            [notice, "提交后需经理确认"] if mixed else [notice]
        )
        if channel == "web":
            assert isinstance(confirm(card).messages[0], bot.PreviewCard)
            history = client.get(
                url + "/messages", params=query, headers=auth["employee"]
            )
            assert history.status_code == 200, history.text
            assert history.json()[0]["payload"]["messages"][0] == legacy
        db.expire_all()
        unchanged = db.get(ProfileChangeRequest, request_id)
        assert unchanged and unchanged.status == "draft"
        assert unchanged.submitted_at is None
        assert (
            current_claims(db, org_id, owner.employee_id)[backend.key].id == original.id
        )
        response = confirm(fresh)
        if mixed:
            first, second = rendered[-1].split("\n\n")
            assert (
                notice in first
                and "提交后需经理确认" in second
                and notice not in second
            )
    (status,) = response.messages
    assert isinstance(status, bot.StatusCard)
    assert status.status == ("pending_review" if mixed else "approved")
    db.expire_all()
    published = current_claims(db, org_id, owner.employee_id)
    assert published[backend.key].assessment_status == "unknown"
    assert published[backend.key].level is None
    if mixed:
        assert published[other.key].assessment_status == "tentative"
