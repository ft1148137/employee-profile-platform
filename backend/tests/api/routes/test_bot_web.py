from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.domains.bot import core
from app.domains.change_request.models import ProfileChangeRequest
from app.domains.model.pi_client import TurnResult
from tests.api.routes.test_employee_requests import payload
from tests.domains.test_bot_core import enable
from tests.utils.bot import final_response


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
    assert (
        final_response(response)["messages"][1]["actions"][0]["action"]
        == "confirm_submit"
    )


@pytest.mark.parametrize("channel", ["web", "feishu"])
@pytest.mark.parametrize("role", ["employee", "manager"])
def test_assistant_identity_and_tool_boundaries(
    demo: tuple,
    db: Session,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    channel: str,
    role: str,
) -> None:
    import asyncio
    from uuid import uuid4

    from fastapi import HTTPException

    from app.core.db import engine
    from app.domains.bot import schemas as s
    from app.domains.bot import store, tools
    from app.domains.change_request import employee
    from app.domains.change_request.schemas import ItemInput
    from app.domains.im import binding, gateway
    from app.domains.im.adapters.base import Event
    from app.domains.im.models import ImSetting
    from tests.acceptance.test_m4_3_assistant_import import FakeFeishu
    from tests.api.routes.test_reviews import actor, pending
    from tests.domains.test_im_gateway import enable as enable_im

    _, people, auth = demo
    owner, foreign_request = pending(
        db,
        demo,
        role="manager.platform" if role == "employee" else "employee.algorithm",
        items=[payload()],
    )
    p = actor(db, owner.org_id, f"{role}.platform@demo.example.com")
    enable(db, p.org_id)
    own = employee.save_draft(
        p, db, None, [ItemInput(**payload(statement="只属于本人的判断"))], "web"
    )
    db.commit()
    query = {"org_id": str(p.org_id)}
    session = store.create_session(db, p, channel, "employee", "12345")
    exercised = []

    def check(_config: object, **kwargs: object) -> TurnResult:
        call = kwargs["on_tool_call"]
        # The model cannot change the authenticated subject, even for reads.
        for name, args in (
            ("get_my_profile", {}),
            ("list_my_evidence", {}),
            ("get_my_requests", {}),
            ("draft_update_request", {"items": [payload()]}),
        ):
            for field in ("employee_id", "subject_employee_id"):
                assert "error" in call(name, {**args, field: str(owner.employee_id)})
        profile = call("get_my_profile", {})
        assert profile["items"] == tools.redact(
            s.Profile.model_validate(employee.my_profile(p, db)).model_dump(
                mode="json"
            )["items"]
        )
        evidence = call("list_my_evidence", {})
        assert evidence == tools.redact(
            s.EvidenceList.model_validate(employee.my_evidence(p, db)).model_dump(
                mode="json"
            )
        )
        requests = call("get_my_requests", {"status": "all"})
        assert str(own.id) in [r["id"] for r in requests["data"]]
        assert str(foreign_request.id) not in [r["id"] for r in requests["data"]]
        with pytest.raises(HTTPException) as error:
            call("preview_request", {"request_id": str(foreign_request.id)})
        assert error.value.status_code == 404
        assert call("list_pending_reviews", {}) == {"data": [], "count": 0}
        assert (
            call(
                "prepare_review_decision",
                {
                    "request_id": str(foreign_request.id),
                    "revision": foreign_request.revision,
                    "decisions": [
                        {"item_id": str(i.id), "decision": "approved"}
                        for i in foreign_request.items
                    ],
                },
            )
            is None
        )
        forbidden = (
            "update_model_setting",
            "update_bot_settings",
            "draft_dictionary_from_material",
            "set_department_capabilities",
            "get_department_capabilities",
        )
        names = {t.name for t in kwargs["tools"]}
        assert not names.intersection(forbidden)
        for name in forbidden:
            assert "不允许调用此工具" in call(name, {})["error"]
        exercised.append(True)
        return TurnResult(
            text="已检查边界", stop_reason="stop", model_calls=1, tool_calls=1
        )

    monkeypatch.setattr(core.pi_client, "run_turn", check)
    if channel == "web":
        final_response(
            client.post(
                f"/api/v1/bot/sessions/{session.id}/turns",
                params=query,
                headers=auth[role],
                json={"text": "尝试访问别人的数据"},
            )
        )
    else:
        setting = enable_im(db, p)
        db.refresh(setting)
        setting = ImSetting(**{**setting.model_dump(), "platform": "feishu"})
        db.add(setting)
        db.commit()
        assert binding.bind(
            db,
            p.org_id,
            "feishu",
            "employee",
            "employee",
            "12345",
            binding.create_code(db, p).code,
        )
        incoming = Event(
            p.org_id,
            "feishu",
            uuid4().hex,
            "employee",
            "employee",
            True,
            "尝试访问别人的数据",
        )
        asyncio.run(
            gateway.Gateway(engine).handle(
                incoming, "12345", setting.credentials, FakeFeishu()
            )
        )
    assert exercised == [True]
    # Even a forged preview stored in the current conversation cannot grant ownership.
    card = tools.preview_card(db, owner, foreign_request.id)
    core._save(db, session, [card])
    db.commit()
    for action in ("confirm_submit", "withdraw"):
        body = s.RequestAction(
            action=action,
            request_id=foreign_request.id,
            revision=foreign_request.revision,
        )
        if channel == "web":
            assert (
                client.post(
                    f"/api/v1/bot/sessions/{session.id}/actions",
                    params=query,
                    headers=auth[role],
                    json=body.model_dump(mode="json"),
                ).status_code
                == 404
            )
        else:
            from datetime import timedelta

            from app.domains.base import utc_now
            from app.domains.im.models import ImAction
            from tests.domains.test_im_conversation import button

            row = ImAction(
                org_id=p.org_id,
                platform="feishu",
                token=uuid4().hex[:22],
                bot_identity="12345",
                employee_id=p.employee_id,
                session_id=session.id,
                external_chat_id="employee",
                message_id="forged",
                action=body.model_dump(mode="json"),
                expires_at=utc_now() + timedelta(minutes=5),
            )
            db.add(row)
            db.commit()
            assert (
                gateway.Gateway(engine).dispatch(
                    button(incoming, row), "12345", setting.credentials
                )
                == "操作未完成，请重新查看后再试。"
            )
            db.refresh(row)
            assert row.consumed_at is None
