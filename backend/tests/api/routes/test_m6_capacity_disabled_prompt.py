from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete

from app.domains.bot.prompts import system_prompt
from app.domains.capacity.models import CapacitySetting, CapacityWorkload
from tests.api.routes.test_employee_requests import principal_for


@pytest.fixture(autouse=True)
def cleanup_capacity(demo: tuple, db: Session) -> Generator[None]:
    yield
    db.rollback()
    for model in (CapacityWorkload, CapacitySetting):
        db.execute(delete(model).where(model.org_id == demo[1]["E02"].org_id))
    db.commit()


@pytest.mark.parametrize("channel", ["web", "feishu"])
def test_capacity_prompt_follows_live_setting(
    demo: tuple, db: Session, client: TestClient, channel: str
) -> None:
    url, people, auth = demo
    principal = principal_for(db, people["E02"].org_id)
    for enabled in (True, False, True):
        response = client.put(
            f"{url}/capacity-settings",
            headers=auth["admin"],
            json={"enabled": enabled},
        )
        assert response.status_code == 200
        prompt = system_prompt(db, principal, channel)
        if enabled:
            assert "请到网页「需求与容量」操作" in prompt
            assert "prepare_workload_request" in prompt
        else:
            assert "需求与容量功能已关闭，如需使用请联系企业管理员" in prompt
            assert "请到网页「需求与容量」" not in prompt
            assert "/capacity" not in prompt
            assert "prepare_workload_request" not in prompt


def test_direct_adjustment_rejects_unused_note(demo: tuple, client: TestClient) -> None:
    url, people, auth = demo
    response = client.put(
        f"{url}/capacity-settings", headers=auth["admin"], json={"enabled": True}
    )
    assert response.status_code == 200
    response = client.put(
        f"{url}/capacity/workloads/{people['E02'].id}",
        headers=auth["manager"],
        json={"base_percent": 40, "note": "合成说明"},
    )
    assert response.status_code == 422
    assert any(
        error["loc"] == ["body", "note"] and error["type"] == "extra_forbidden"
        for error in response.json()["detail"]
    )
