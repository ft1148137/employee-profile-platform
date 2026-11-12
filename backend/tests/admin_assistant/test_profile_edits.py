import json
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.domains.admin_assistant import change_set as c
from app.domains.admin_assistant import store, tools
from app.domains.admin_assistant.schemas import ProposeChanges
from app.domains.change_request.models import ProfileChangeRequest
from app.domains.evidence.models import Evidence
from tests.admin_assistant.test_materials import official
from tests.admin_assistant.test_other_files import snapshot
from tests.admin_assistant.test_registry import principal
from tests.api.routes.test_direct_profile_edits import edit_body


def args(db: Session, demo: tuple) -> dict:
    subject = demo[1]["E02"]
    return {"employee_id": str(subject.id), **edit_body(db, subject)}


def propose(db: Session, demo: tuple) -> UUID:
    p = principal(db, demo)
    sid = store.create_session(db, p).id
    row = c.propose(
        db,
        p,
        sid,
        ProposeChanges.model_validate(
            {
                "items": [
                    {
                        "function": "edit_employee_profile",
                        "args": args(db, demo),
                    }
                ]
            }
        ),
    )
    db.commit()
    return row.id


def test_confirmation_publication_and_private_content(
    db: Session, demo: tuple, client: TestClient
) -> None:
    p = principal(db, demo)
    before, versions = snapshot(db, p.org_id), official(db, p.org_id)
    id_ = propose(db, demo)
    row = c.get(db, p, id_)
    card = c.public(row)
    text = card.items[0].impact.text
    assert demo[1]["E02"].display_name in text
    assert "等级" in text and "→ routine" in text and "直接发布并通知本人" in text
    assert card.items[0].risk == "high" and not card.items[0].undoable
    private = [
        e.content[key]
        for e in db.exec(
            select(Evidence).where(
                Evidence.org_id == p.org_id,
                Evidence.subject_employee_id == demo[1]["E02"].id,
            )
        ).all()
        for key in ("text", "comment")
        if e.content.get(key)
    ]
    assert private and all(t not in card.model_dump_json() for t in private)
    assert snapshot(db, p.org_id) == before and official(db, p.org_id) == versions
    url = f"{demo[0]}/admin-assistant/change-sets/{id_}/apply"
    for role in ("employee", "manager", "foreign"):
        assert (
            client.post(
                url, headers=demo[2][role], json={"revision": row.revision}
            ).status_code
            == 404
        )
    assert c.apply(db, p, id_, row.revision).status == "draft"
    assert snapshot(db, p.org_id) == before and official(db, p.org_id) == versions
    result = c.apply(db, p, id_, row.revision)
    assert result.status == "applied" and official(db, p.org_id) != versions
    assert all(t not in result.model_dump_json() for t in private)
    added = snapshot(db, p.org_id)
    assert added[-1] == before[-1]
    assert len(added[2]) == len(before[2]) + 1
    assert added[2][-1]["kind"] == "profile_edited"
    audit = next(i for i in added[3] if i["action"] == "profile.direct_edited")
    assert all(t not in json.dumps(audit, ensure_ascii=False) for t in private)
    request = db.exec(
        select(ProfileChangeRequest).where(
            ProfileChangeRequest.org_id == p.org_id,
            ProfileChangeRequest.client_name == "管理助手",
        )
    ).one()
    assert request.status == "approved" and request.submitted_via == "web"
    with pytest.raises(c.ChangeSetError, match="不能撤销"):
        c.undo(db, p, id_)


@pytest.mark.parametrize("problem", ["self", "foreign", "missing", "stale"])
def test_preview_rejects_invalid_target_or_revision(
    db: Session, demo: tuple, problem: str
) -> None:
    p = principal(db, demo)
    sid = store.create_session(db, p).id
    body = args(db, demo)
    expected = "找不到"
    if problem == "self":
        p = replace(p, employee_id=demo[1]["E02"].id)
        expected = "不能直接修改自己的画像"
    elif problem == "stale":
        body["base_revision"] += 1
        expected = "画像已变化"
    else:
        subject = next(iter(demo[1].values()))
        if problem == "foreign":
            from app.domains.org.models import Employee

            subject = db.exec(
                select(Employee).where(Employee.org_id != p.org_id)
            ).first()
        body["employee_id"] = str(subject.id if problem == "foreign" else uuid4())
        expected = "找不到这份改动或会话"
    before = snapshot(db, p.org_id)
    cards = []
    result = tools.call(
        db,
        p,
        cards,
        "propose_changes",
        {
            "items": [
                {
                    "function": "edit_employee_profile",
                    "args": body,
                }
            ]
        },
        session_id=sid,
    )
    assert expected in result["error"]
    assert not cards and snapshot(db, p.org_id) == before


def test_confirmation_rejects_stale_version(db: Session, demo: tuple) -> None:
    p = principal(db, demo)
    first = c.get(db, p, propose(db, demo))
    c.apply(db, p, first.id, first.revision)
    second = c.get(db, p, propose(db, demo))
    c.apply(db, p, second.id, second.revision)
    c.apply(db, p, second.id, second.revision)
    before, versions = snapshot(db, p.org_id), official(db, p.org_id)
    with pytest.raises(c.ChangeSetError, match="画像已变化") as conflict:
        c.apply(db, p, first.id, first.revision)
    assert conflict.value.status_code == 409
    assert snapshot(db, p.org_id) == before and official(db, p.org_id) == versions
    assert c.get(db, p, first.id).status == "draft"
