from dataclasses import replace
from datetime import timedelta
from io import BytesIO
from typing import Any
from uuid import UUID

import pytest
from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlmodel import Session, select

from app.domains.admin_assistant import change_set as c
from app.domains.admin_assistant import core, store, tools
from app.domains.admin_assistant.models import AdminChangeSet
from app.domains.admin_assistant.schemas import (
    AdminTurnInput,
    AdminTurnResponse,
    ProposeChanges,
)
from app.domains.audit.models import AuditEvent
from app.domains.base import utc_now
from app.domains.bot import attachments
from app.domains.capability.publish import save_dictionary
from app.domains.capability.service import read_editor
from app.domains.change_request.import_json import InvalidImport
from app.domains.model.pi_client import TurnResult
from tests.admin_assistant.test_materials import official
from tests.admin_assistant.test_registry import principal
from tests.domains.test_bot_core import enable


def capability(key: str = "synthetic") -> dict:
    return {
        "key": key,
        "name": "合成能力" + key,
        "category": "工程",
        "description": "合成说明",
        "levels": [{"key": "basic", "label": "基础", "description": "指导下完成"}],
    }


def args(capabilities: list[dict], mode: str = "merge") -> dict:
    return {
        "materials": [{"attachment_id": None, "label": "管理员描述"}],
        "mode": mode,
        "capabilities": capabilities,
    }


def propose(
    db: Session, demo: tuple, values: dict, sid: UUID | None = None
) -> AdminChangeSet:
    p = principal(db, demo)
    row = c.propose(
        db,
        p,
        sid or store.create_session(db, p).id,
        ProposeChanges.model_validate(
            {"items": [{"function": "draft_dictionary_from_material", "args": values}]}
        ),
    )
    db.commit()
    return row


@pytest.mark.parametrize("source", ["xlsx", "description"])
def test_model_material_confirmation(
    db: Session, demo: tuple, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    p = principal(db, demo)
    enable(db, p.org_id)
    sid = store.create_session(db, p).id
    before = read_editor(db, p)
    published = official(db, p.org_id)
    ids = []
    values = args([capability(f"synthetic_{n}") for n in range(3)])
    if source == "xlsx":
        book = Workbook()
        assert book.active is not None
        book.active.append(["技能", "说明", "初级", "中级", "高级"])
        for item in values["capabilities"]:
            book.active.append(
                [item["name"], "合成说明", "指导下完成", "独立完成", "指导他人"]
            )
        content = BytesIO()
        book.save(content)
        book.close()
        attachment = attachments.upload(
            db,
            p,
            "admin",
            sid,
            UploadFile(filename="技能.xlsx", file=BytesIO(content.getvalue())),
        )
        ids = [attachment.id]
        values["materials"] = [
            {"label": "技能.xlsx", "attachment_id": str(attachment.id)}
        ]
    else:
        item = before.definitions[0].model_dump(mode="json")
        item["levels"] = item.pop("level_rubric")["levels"]
        item["levels"][0]["description"] = "独立完成合成任务"
        values = args([item])

    def reply(_config: object, **kwargs: Any) -> TurnResult:
        assert "词典类材料用 draft_dictionary_from_material" in kwargs["system_prompt"]
        if ids:
            assert "技能" in kwargs["input"] and str(ids[0]) in kwargs["input"]
        result = kwargs["on_tool_call"](
            "propose_changes",
            {"items": [{"function": "draft_dictionary_from_material", "args": values}]},
        )
        assert "error" not in result, result
        return TurnResult(
            text="请核对词典", stop_reason="stop", model_calls=1, tool_calls=1
        )

    monkeypatch.setattr(core.pi_client, "run_turn", reply)
    response = core.turn(
        db,
        p,
        sid,
        AdminTurnInput(
            text="按材料整理词典",
            page="/admin/rules?tab=dictionary",
            attachment_ids=ids,
        ),
    )
    card = response.messages[1].change_set
    assert read_editor(db, p) == before
    assert official(db, p.org_id) == published
    assert ("新增 3 项" if ids else "修改 1 项") in card.items[0].impact.text
    assert card.items[0].risk == "normal"
    assert c.apply(db, p, card.id, card.revision).status == "applied"
    after = read_editor(db, p)
    assert after.expected_version == before.expected_version + 1
    assert len(after.definitions) == len(before.definitions) + (3 if ids else 0)
    if not ids:
        assert (
            after.definitions[0].level_rubric.levels[0].description
            == "独立完成合成任务"
        )
    assert official(db, p.org_id) == published
    event = db.exec(
        select(AuditEvent)
        .where(
            AuditEvent.org_id == p.org_id,
            AuditEvent.action == "capability_schema.activated",
        )
        .order_by(AuditEvent.created_at.desc())
    ).first()
    assert event.details["material_names"] == [values["materials"][0]["label"]]
    assert event.details["assistant_session_id"] == str(sid)
    assert c.undo(db, p, card.id).status == "applied"
    assert read_editor(db, p).definitions == before.definitions


def test_replace_preserves_claims_and_requires_second_confirmation(
    db: Session, demo: tuple
) -> None:
    p = principal(db, demo)
    published = official(db, p.org_id)
    row = propose(db, demo, args([capability()], "replace"))
    impact = row.items[0]["impact"]["text"]
    assert row.items[0]["risk"] == "high" and "条判断保留" in impact
    assert any(d.name in impact for d in read_editor(db, p).definitions)
    assert c.apply(db, p, row.id, row.revision).status == "draft"
    assert official(db, p.org_id) == published
    assert c.apply(db, p, row.id, row.revision).status == "applied"
    assert official(db, p.org_id) == published
    assert [d.key for d in read_editor(db, p).definitions] == ["synthetic"]


@pytest.mark.parametrize("count,expected", [(9, "normal"), (10, "high")])
def test_actual_change_risk(
    db: Session, demo: tuple, count: int, expected: str
) -> None:
    p = principal(db, demo)
    existing = []
    for d in read_editor(db, p).definitions:
        item = d.model_dump(mode="json")
        item["levels"] = item.pop("level_rubric")["levels"]
        existing.append(item)
    row = propose(
        db, demo, args(existing + [capability(f"new_{n}") for n in range(count)])
    )
    assert row.items[0]["risk"] == expected
    assert row.items[0]["impact"]["count"] == count


@pytest.mark.parametrize("failure", ["key", "levels"])
def test_invalid_values_are_reported_without_writes(
    db: Session, demo: tuple, failure: str
) -> None:
    p = principal(db, demo)
    before = read_editor(db, p)
    item = capability()
    if failure == "key":
        item["key"] = "Invalid key!"
    else:
        item["levels"] *= 2
    result = tools.call(
        db,
        p,
        [],
        "propose_changes",
        {
            "items": [
                {"function": "draft_dictionary_from_material", "args": args([item])}
            ]
        },
        session_id=store.create_session(db, p).id,
    )
    assert ("pattern" if failure == "key" else "等级 key 重复") in result["error"]
    assert read_editor(db, p) == before


def test_stale_card_and_attachment_boundaries(
    db: Session, demo: tuple, client: TestClient
) -> None:
    p = principal(db, demo)
    sid = store.create_session(db, p).id
    attachment = attachments.upload(
        db,
        p,
        "admin",
        sid,
        UploadFile(filename="词典.txt", file=BytesIO("合成词典".encode())),
    )
    values = args([capability()])
    values["materials"] = [{"label": "词典.txt", "attachment_id": str(attachment.id)}]
    with pytest.raises(InvalidImport, match="附件不属于当前会话"):
        propose(db, demo, values, store.create_session(db, p).id)
    row = propose(db, demo, values, sid)
    for role in ("manager", "employee", "foreign"):
        response = client.post(
            f"/api/v1/admin-assistant/change-sets/{row.id}/apply",
            params={"org_id": str(p.org_id)},
            headers=demo[2][role],
            json={"revision": row.revision},
        )
        assert response.status_code in (403, 404)
    with pytest.raises(HTTPException) as caught:
        c.propose(
            db,
            replace(p, is_org_admin=False),
            sid,
            ProposeChanges.model_validate(
                {
                    "items": [
                        {"function": "draft_dictionary_from_material", "args": values}
                    ]
                }
            ),
        )
    assert caught.value.status_code in (403, 404)
    from app.domains.capability.schemas import SaveDictionary

    editor = read_editor(db, p)
    save_dictionary(
        db,
        p,
        SaveDictionary(
            definitions=editor.definitions, expected_version=editor.expected_version
        ),
    )
    with pytest.raises(c.ChangeSetError, match="已变化"):
        c.apply(db, p, row.id, row.revision)
    assert read_editor(db, p).definitions == editor.definitions


def test_retired_upload_and_unchanged_export(
    db: Session, demo: tuple, client: TestClient
) -> None:
    p = principal(db, demo)
    before = client.get(demo[0] + "/capabilities/export", headers=demo[2]["admin"])
    sid = store.create_session(db, p).id
    assert client.get(
        demo[0] + "/capabilities/template", headers=demo[2]["admin"]
    ).status_code in (404, 405)
    response = client.post(
        f"/api/v1/admin-assistant/sessions/{sid}/files",
        params={"org_id": str(p.org_id)},
        headers=demo[2]["admin"],
        data={"kind": "capabilities"},
        files={"file": ("词典.txt", b"synthetic")},
    )
    assert response.status_code == 422
    after = client.get(demo[0] + "/capabilities/export", headers=demo[2]["admin"])
    assert (
        before.status_code == after.status_code == 200
        and before.content == after.content
    )
    old = AdminTurnResponse(messages=[{"type": "file_upload", "kind": "capabilities"}])
    assert "词典上传框已下线" in old.messages[0].text


def test_expired_attachment_cannot_apply(db: Session, demo: tuple) -> None:
    p = principal(db, demo)
    sid = store.create_session(db, p).id
    attachment = attachments.upload(
        db, p, "admin", sid, UploadFile(filename="词典.txt", file=BytesIO(b"synthetic"))
    )
    values = args([capability()])
    values["materials"] = [{"label": "词典.txt", "attachment_id": str(attachment.id)}]
    row = propose(db, demo, values, sid)
    before = read_editor(db, p)
    record = attachments.get_session_attachment(db, p, "admin", sid, attachment.id)
    record.created_at = utc_now() - timedelta(days=366)
    db.add(record)
    db.commit()
    with pytest.raises(c.ChangeSetError, match="附件不属于当前会话或已过期"):
        c.apply(db, p, row.id, row.revision)
    assert read_editor(db, p) == before


def test_dictionary_changes_cannot_share_a_material_card(
    db: Session, demo: tuple
) -> None:
    p = principal(db, demo)
    with pytest.raises(c.ChangeSetError, match="只能保存一次词典"):
        c.propose(
            db,
            p,
            store.create_session(db, p).id,
            ProposeChanges.model_validate(
                {
                    "items": [
                        {
                            "function": "draft_dictionary_from_material",
                            "args": args([capability()]),
                        },
                        {
                            "function": "apply_capability_template",
                            "args": {"template_id": "tech-rnd"},
                        },
                    ]
                }
            ),
        )
