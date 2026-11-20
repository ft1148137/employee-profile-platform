import asyncio
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.db import engine
from app.domains.bot import core
from app.domains.capacity.models import CapacitySetting
from app.domains.im import binding, gateway
from app.domains.im.adapters.base import Event
from tests.acceptance.test_m4_3_assistant_import import FakeFeishu
from tests.domains.test_bot_core import enable
from tests.domains.test_im_gateway import enable as enable_im
from tests.domains.test_im_gateway import principal
from tests.utils.bot import final_response
from tests.utils.pi import PiHarness

pytestmark = pytest.mark.usefixtures("reset_login_attempts")

DISABLED = "规划与负荷功能未开启，如需使用请联系企业管理员。"


@pytest.mark.parametrize("channel", ["web", "feishu"])
def test_capacity_switch_updates_next_turn_prompt(
    demo: tuple,
    db: Session,
    client: TestClient,
    pi_service: PiHarness,
    monkeypatch: pytest.MonkeyPatch,
    channel: str,
) -> None:
    _, people, auth = demo
    p = principal(db, people["E02"].org_id, people["E02"].id)
    enable(db, p.org_id)
    monkeypatch.setattr(core, "model_config", lambda _: pi_service.config)
    base = "/api/v1/bot/sessions"
    params = {"org_id": str(p.org_id)}
    if channel == "web":
        response = client.post(base, params=params, headers=auth["employee"])
        assert response.status_code == 201, response.text
        session_id = response.json()["session_id"]
    else:
        setting = enable_im(db, p)
        setting.platform = "feishu"
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
    capacity = CapacitySetting(
        org_id=p.org_id, enabled=True, updated_by_user_id=p.user_id
    )
    try:
        # No row, enabled, then disabled: each turn must read the current setting.
        for enabled in (None, True, False):
            if enabled is not None:
                capacity.enabled = enabled
                db.add(capacity)
                db.commit()
            pi_service.replies = ["合成答复"]
            if channel == "web":
                final_response(
                    client.post(
                        f"{base}/{session_id}/turns",
                        params=params,
                        headers=auth["employee"],
                        json={"text": "我在哪里看负荷和延期？"},
                    )
                )
            else:
                asyncio.run(
                    gateway.Gateway(engine).handle(
                        Event(
                            p.org_id,
                            "feishu",
                            uuid4().hex,
                            "employee",
                            "employee",
                            True,
                            "我在哪里看负荷和延期？",
                        ),
                        "12345",
                        setting.credentials,
                        FakeFeishu(),
                    )
                )
            messages = pi_service.requests[-1]["body"]["messages"]
            prompt = next(m["content"] for m in messages if m["role"] == "system")
            if enabled:
                assert "规划与负荷功能只使用当前可用的容量工具" in prompt
                assert "请到网页「协作需求」详情操作。" in prompt
                assert DISABLED not in prompt
            else:
                assert DISABLED in prompt
                assert "请到网页「协作需求」详情操作。" not in prompt
                assert "/capacity" not in prompt
                assert "prepare_workload_request" not in prompt
    finally:
        db.rollback()
        if capacity in db:
            db.delete(capacity)
            db.commit()
