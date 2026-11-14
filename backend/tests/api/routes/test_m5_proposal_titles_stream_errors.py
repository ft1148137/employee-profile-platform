import json
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.domains.admin_assistant import core as admin_core
from app.domains.audit.models import ActorKind
from app.domains.base import utc_now
from app.domains.bot import core
from app.domains.bot.admission import (
    BotBusy,
    BotDraftingPriority,
    BotQuotaPaused,
    BotRateLimited,
)
from app.domains.bot.store import BotSessionNotFound
from app.domains.capability.models import CapabilityProposal, CapabilityProposalEvidence
from app.domains.evidence.models import Evidence, EvidenceKind, Visibility
from app.domains.evidence.service import retract_evidence, supersede_evidence
from app.domains.model import pi_client
from app.domains.model.client import (
    ModelRateLimited,
    ModelServiceError,
    ModelUnavailable,
)
from tests.domains.test_bot_core import enable

pytestmark = pytest.mark.xdist_group("model")
PRIVATE = "synthetic-upstream-secret-sk-test"


def test_proposal_titles_follow_active_evidence(
    db: Session, client: TestClient, demo: tuple
) -> None:
    url, people, auth = demo
    employee = people["E02"]
    proposal = CapabilityProposal(
        org_id=employee.org_id,
        name="合成能力建议",
        name_key="合成能力建议",
        description="合成说明",
        reason="合成理由",
    )
    manual = CapabilityProposal(
        org_id=employee.org_id,
        name="无依据的手工建议",
        name_key="无依据的手工建议",
        description="合成说明",
        reason="合成理由",
        proposed_by_employee_id=employee.id,
    )
    db.add_all([proposal, manual])
    db.flush()
    evidence = []
    for i in range(2):
        row = Evidence(
            org_id=employee.org_id,
            subject_employee_id=employee.id,
            kind=EvidenceKind.WORK_RECORD,
            source="synthetic",
            source_ref=f"m5-{i}",
            title=f"合成原始依据{i}",
            content={"text": "合成正文"},
            visibility=Visibility.STANDARD,
            provenance={},
            content_hash=f"m5-{i}",
        )
        db.add(row)
        db.flush()
        db.add(
            CapabilityProposalEvidence(
                org_id=employee.org_id, proposal_id=proposal.id, evidence_id=row.id
            )
        )
        evidence.append(row)
    db.commit()

    def listed() -> dict:
        response = client.get(f"{url}/capabilities/proposals", headers=auth["admin"])
        assert response.status_code == 200
        return {item["id"]: item for item in response.json()["data"]}

    assert set(listed()[str(proposal.id)]["evidence_titles"]) == {
        row.title for row in evidence
    }
    retract_evidence(
        db,
        evidence=evidence[0],
        reason="合成撤回",
        actor_type=ActorKind.USER,
        actor_id=None,
    )
    db.commit()
    mixed = listed()[str(proposal.id)]
    assert mixed["evidence_titles"] == [evidence[1].title]
    assert mixed["evidence_count"] == 1 and mixed["employee_count"] == 1
    assert mixed["evidence_withdrawn"] is False
    supersede_evidence(
        db,
        original=evidence[1],
        title="合成更正后的依据",
        content={"text": "合成更正"},
        occurred_at=None,
        event_id=None,
        provenance={},
        actor_id=None,
        reason="合成更正",
        actor_type=ActorKind.USER,
    )
    db.commit()
    rows = listed()
    withdrawn = rows[str(proposal.id)]
    assert withdrawn["evidence_titles"] == []
    assert withdrawn["evidence_count"] == 0 and withdrawn["employee_count"] == 0
    assert withdrawn["evidence_withdrawn"] is True
    assert rows[str(manual.id)]["evidence_withdrawn"] is False
    assert "合成原始依据" not in json.dumps(rows, ensure_ascii=False)


@pytest.mark.parametrize("assistant", ["bot", "admin-assistant"])
@pytest.mark.parametrize(
    "failure,expected",
    [
        (ModelUnavailable(PRIVATE), "模型服务暂时不可用"),
        (ModelUnavailable(PRIVATE, code="deadline"), "超过 3 分钟"),
        (ModelServiceError("model_not_found"), "这个模型不可用"),
        (
            ModelRateLimited(utc_now() + timedelta(minutes=10), PRIVATE),
            "模型额度暂时用完",
        ),
        (TimeoutError(PRIVATE), "超时"),
        (BotBusy(PRIVATE), "系统正忙"),
        (BotDraftingPriority(PRIVATE), "正在起草画像"),
        (BotRateLimited(utc_now() + timedelta(minutes=10)), "对话次数已达上限"),
        (BotQuotaPaused(utc_now() + timedelta(minutes=10), True), "模型额度暂时用完"),
        (BotSessionNotFound(), "会话已过期"),
        (RuntimeError(PRIVATE), "对话暂时出错"),
    ],
)
def test_worker_errors_are_safe_chinese_events(
    client: TestClient,
    demo: tuple,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    assistant: str,
    failure: Exception,
    expected: str,
) -> None:
    query = {"org_id": str(demo[1]["E02"].org_id)}
    headers = demo[2]["employee" if assistant == "bot" else "admin"]
    url = f"/api/v1/{assistant}/sessions"
    sid = client.post(url, params=query, headers=headers).json()["session_id"]

    def fail(*_args: Any, **kwargs: Any) -> Any:
        kwargs["stream"].on_text("未完成的回复")
        raise failure

    monkeypatch.setattr(core if assistant == "bot" else admin_core, "turn", fail)
    response = client.post(
        f"{url}/{sid}/turns",
        params=query,
        headers=headers,
        json={"text": "合成问题", **({"page": "/admin"} if assistant != "bot" else {})},
    )
    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines()]
    assert [event["type"] for event in events] == ["text", "error"]
    assert expected in events[-1]["message"]
    assert PRIVATE not in response.text
    assert PRIVATE not in caplog.text


@pytest.mark.parametrize("assistant", ["bot", "admin-assistant"])
@pytest.mark.parametrize("limited", [False, True])
def test_model_failures_keep_existing_safe_final_reply(
    db: Session,
    client: TestClient,
    demo: tuple,
    monkeypatch: pytest.MonkeyPatch,
    assistant: str,
    limited: bool,
) -> None:
    org_id = demo[1]["E02"].org_id
    enable(db, org_id)
    query = {"org_id": str(org_id)}
    headers = demo[2]["employee" if assistant == "bot" else "admin"]
    url = f"/api/v1/{assistant}/sessions"
    sid = client.post(url, params=query, headers=headers).json()["session_id"]

    def fail(*_args: Any, **kwargs: Any) -> Any:
        kwargs["stream"].on_text("未完成的回复")
        if limited:
            raise ModelRateLimited(utc_now() + timedelta(minutes=10), PRIVATE)
        raise ModelUnavailable(PRIVATE)

    monkeypatch.setattr(pi_client, "run_turn", fail)
    response = client.post(
        f"{url}/{sid}/turns",
        params=query,
        headers=headers,
        json={"text": "合成问题", **({"page": "/admin"} if assistant != "bot" else {})},
    )
    final = json.loads(response.text.splitlines()[-1])
    assert final["type"] == "final"
    message = final["response"]["messages"][0]["text"]
    assert (
        "重试" if limited and assistant != "bot" else "恢复" if limited else "不可用"
    ) in message
    assert PRIVATE not in response.text
