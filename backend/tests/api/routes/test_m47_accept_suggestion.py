from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.domains.capability.models import CapabilityProposal, ProposalStatus


def test_accepted_employee_suggestion_is_available_for_profile_updates(
    demo: tuple, client: TestClient, db: Session
) -> None:
    url, employees, auth = demo
    base = f"{url}/capabilities"
    suggestion = {
        "name": "研发费用归集",
        "description": "按研发项目归集费用",
        "reason": "合成工作需要",
    }
    assert (
        client.post(
            f"{base}/proposals", headers=auth["employee"], json=suggestion
        ).status_code
        == 200
    )
    editor = client.get(f"{base}/editor", headers=auth["admin"]).json()
    definitions = editor["definitions"]
    # The editor supplies the identifier and rubric; the admin only reviews the text.
    definitions.append(
        {
            **definitions[0],
            "key": "capability_1",
            "name": "已有自动编号能力",
        }
    )
    initial = client.put(
        base,
        headers=auth["admin"],
        json={
            "definitions": definitions,
            "expected_version": editor["expected_version"],
        },
    )
    assert initial.status_code == 200, initial.text
    editor = client.get(f"{base}/editor", headers=auth["admin"]).json()
    rubric = editor["definitions"][0]["level_rubric"]
    assert len(rubric["levels"]) == 3
    assert all(level["description"] for level in rubric["levels"])
    definitions = [
        *editor["definitions"],
        {
            "key": "capability_2",
            "name": suggestion["name"],
            "description": suggestion["description"],
            "category": "未分类",
            "level_rubric": rubric,
        },
    ]
    body = {"definitions": definitions, "expected_version": editor["expected_version"]}
    preview = client.post(f"{base}/editor/preview", headers=auth["admin"], json=body)
    assert preview.status_code == 200, preview.text
    assert preview.json()["added"] == ["capability_2"]
    saved = client.put(base, headers=auth["admin"], json=body)
    assert saved.status_code == 200, saved.text
    row = db.exec(
        select(CapabilityProposal).where(
            CapabilityProposal.org_id == employees["E02"].org_id,
            CapabilityProposal.name == suggestion["name"],
        )
    ).one()
    assert row.status == ProposalStatus.ACCEPTED
    assert row.decided_by_user_id is not None and row.decided_at is not None
    assert client.get(f"{base}/proposals", headers=auth["admin"]).json()["data"] == []
    available = client.get(f"{url}/me/capabilities", headers=auth["employee"])
    assert available.status_code == 200, available.text
    added = next(
        item for item in available.json()["data"] if item["key"] == "capability_2"
    )
    assert added["name"] == suggestion["name"] and added["level_rubric"] == rubric
    draft = client.post(
        f"{url}/me/requests",
        headers=auth["employee"],
        json={
            "items": [
                {
                    "op": "add",
                    "capability_key": added["key"],
                    "statement": "我能按研发项目归集费用",
                    "level": rubric["levels"][0]["key"],
                }
            ],
        },
    )
    assert draft.status_code == 201, draft.text
    assert draft.json()["items"][0]["capability"]["key"] == "capability_2"
