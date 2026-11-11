import json
from base64 import b64decode
from collections.abc import Generator
from dataclasses import replace
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session, select

from app.authz.deps import get_org_principal
from app.core.secrets import decrypt_secret
from app.domains.admin_assistant import change_set as c
from app.domains.admin_assistant import files, registry, store, tools
from app.domains.admin_assistant.models import AdminChangeSet
from app.domains.admin_assistant.schemas import (
    AdminTurnResponse,
    FilePreviewCard,
    ProposeChanges,
)
from app.domains.audit.models import AuditEvent
from app.domains.bot import store as messages
from app.domains.capability.files import dictionary_xlsx
from app.domains.capability.service import get_schema, read_editor
from app.domains.change_request.import_json import InvalidImport
from app.domains.change_request.models import ChangeItem, ProfileChangeRequest
from app.domains.drafting.models import DraftingSetting
from app.domains.evidence.models import Evidence
from app.domains.inbox.models import InboxItem
from app.domains.org.models import Organization, OrgMembership, Role, RoleGrant
from app.domains.source.import_records import InvalidWorkRecords
from app.domains.source.models import SyncRun
from app.domains.work.records import (
    WorkRecordIssue,
    build_work_records_template_csv,
    parse_work_records,
)
from app.models import User
from tests.admin_assistant.test_registry import principal


def snapshot(db: Session, org_id: UUID) -> list[list[dict]]:
    return [
        [
            row.model_dump(mode="json")
            for row in db.exec(
                select(model).where(model.org_id == org_id).order_by(model.id)
            ).all()
        ]
        for model in (ProfileChangeRequest, ChangeItem, InboxItem, AuditEvent, Evidence)
    ]


def jobs(db: Session, org: UUID) -> int:
    return db.execute(
        text("SELECT count(*) FROM procrastinate_jobs WHERE args->>'org_id'=:org"),
        {"org": str(org)},
    ).scalar_one()


@pytest.fixture(params=["work_csv", "capabilities"])
def file_case(
    request: pytest.FixtureRequest, db: Session, demo: tuple
) -> Generator[tuple]:
    p = principal(db, demo)
    sid = store.create_session(db, p).id
    kind = request.param
    if kind == "work_csv":
        content, filename = build_work_records_template_csv(), "work.csv"
    else:
        definitions = read_editor(db, p).definitions
        definitions[0].name = "合成修改能力"
        content, filename = dictionary_xlsx(definitions[:-1]), "dictionary.xlsx"
        setting = db.get(DraftingSetting, p.org_id) or DraftingSetting(
            org_id=p.org_id, updated_by_user_id=p.user_id
        )
        setting.auto_draft_enabled = False
        db.add(setting)
        db.commit()
    try:
        yield kind, p, sid, filename, content
    finally:
        db.rollback()
        db.execute(
            text("DELETE FROM procrastinate_jobs WHERE args->>'org_id'=:org"),
            {"org": str(p.org_id)},
        )
        db.commit()


def upload(
    client: TestClient,
    demo: tuple,
    case: tuple,
    content: bytes | None = None,
    **kwargs: Any,
) -> Any:
    kind, _, sid, filename, original = case
    return client.post(
        f"/api/v1/admin-assistant/sessions/{sid}/files",
        params={"org_id": case[1].org_id},
        headers=demo[2]["admin"],
        data={"kind": kind},
        files={"file": (filename, original if content is None else content)},
        **kwargs,
    )


def state(db: Session, p: Any) -> tuple:
    db.expire_all()
    return (
        snapshot(db, p.org_id),
        [
            r.model_dump()
            for r in db.exec(select(SyncRun).where(SyncRun.org_id == p.org_id)).all()
        ],
        read_editor(db, p).model_dump(),
        jobs(db, p.org_id),
    )


@pytest.mark.parametrize("assistant", [True, False])
def test_web_parity_and_confirmation(
    db: Session, client: TestClient, demo: tuple, file_case: tuple, assistant: bool
) -> None:
    kind, p, _, filename, content = file_case
    before = state(db, p)
    response = upload(client, demo, file_case)
    assert response.status_code == 200, response.text
    preview, card = response.json()["messages"]
    assert not preview["preview"]["errors"]
    assert state(db, p) == before
    row = c.get(db, p, UUID(card["change_set"]["id"]))
    args = row.items[0]["args"]
    assert row.items[0]["risk"] == "normal"
    if kind == "capabilities":
        web = client.post(
            demo[0] + "/capabilities/preview",
            headers=demo[2]["admin"],
            files={"file": (filename, content)},
        ).json()
        assert (
            args["definitions"] == web["definitions"]
            and args["expected_version"] == web["expected_version"]
        )
        assert preview["preview"]["counts"]["修改"] == len(web["modified"]) == 1
        assert preview["preview"]["counts"]["删除"] == len(web["removed"]) == 1
        assert row.items[0]["function"] == "import_capability_dictionary"
        assert row.secrets is None
    else:
        assert b64decode(json.loads(decrypt_secret(row.secrets))["content"]) == content
        assert row.items[0]["before"] is None and not row.items[0]["undoable"]
        assert "content" not in json.dumps(row.items)
    if assistant:
        assert c.apply(db, p, row.id, row.revision).status == "applied"
        assert c.get(db, p, row.id).secrets is None
        if kind != "capabilities":
            with pytest.raises(c.ChangeSetError, match="文件已失效"):
                c.apply(db, p, row.id, row.revision)
    else:
        path = {
            "work_csv": "/sources/csv/imports",
            "capabilities": "/capabilities/versions",
        }[kind]
        response = client.post(
            demo[0] + path,
            headers=demo[2]["admin"],
            **(
                {"json": args}
                if kind == "capabilities"
                else {
                    "files": {
                        "file": (
                            filename,
                            content,
                        )
                    }
                }
            ),
        )
        assert response.status_code in (200, 202), response.text
    db.expire_all()
    if kind == "work_csv":
        run = db.exec(select(SyncRun).where(SyncRun.org_id == p.org_id)).one()
        assert run.status == "queued"
        assert run.input["rows"] == [
            r.model_dump(mode="json")
            for r in parse_work_records(content, filename).rows
        ]
        assert len(run.input["rows"]) == preview["preview"]["counts"]["记录条数"] == 1
        assert (
            preview["preview"]["counts"]["涉及人数"] == 3
            and jobs(db, p.org_id) == before[-1] + 1
        )
    else:
        expected = args["definitions"]
        assert [
            d.model_dump(mode="json") for d in read_editor(db, p).definitions
        ] == expected
        assert not db.get(DraftingSetting, p.org_id).auto_draft_enabled
        assert get_schema(db, p.org_id).version == before[2]["expected_version"] + 1
        if assistant:
            reverse = c.undo(db, p, row.id)
            assert reverse.status == "applied"
            assert reverse.items[0].function == "import_capability_dictionary"
            assert [d.model_dump() for d in read_editor(db, p).definitions] == before[
                2
            ]["definitions"]
            assert not db.get(DraftingSetting, p.org_id).auto_draft_enabled


def test_invalid_limits_and_private_sessions(
    db: Session, client: TestClient, demo: tuple, file_case: tuple
) -> None:
    kind, p, sid, _, _ = file_case
    before = state(db, p)
    invalid = upload(client, demo, file_case, b"bad file")
    assert invalid.status_code == 200 and len(invalid.json()["messages"]) == 1
    assert invalid.json()["messages"][0]["preview"]["errors"]
    assert (
        db.exec(select(AdminChangeSet).where(AdminChangeSet.session_id == sid)).all()
        == []
    )
    limit = {"work_csv": 5, "capabilities": 2}[kind]
    assert (
        upload(client, demo, file_case, b"x" * (limit * 1024 * 1024 + 1)).status_code
        == 413
    )
    other = principal(db, demo, "employee")
    foreign_id = UUID(
        client.get("/api/v1/users/me", headers=demo[2]["foreign"]).json()["id"]
    )
    for user_id in (other.user_id, foreign_id):
        member = db.exec(
            select(OrgMembership).where(
                OrgMembership.user_id == user_id,
                OrgMembership.org_id == p.org_id
                if user_id == other.user_id
                else OrgMembership.org_id != p.org_id,
            )
        ).one()
        db.add(
            RoleGrant(
                org_id=member.org_id, membership_id=member.id, role=Role.ORG_ADMIN
            )
        )
        db.commit()
        who = get_org_principal(member.org_id, db.get(User, user_id), db)
        # Session ownership is enforced before parsing the uploaded bytes.
        other_sid = store.create_session(db, who).id
        assert (
            upload(
                client, demo, (kind, p, other_sid, file_case[3], file_case[4])
            ).status_code
            == 404
        )
    assert state(db, p) == before


@pytest.mark.parametrize("file_case", ["work_csv"], indirect=True)
@pytest.mark.parametrize("action", ["discard", "stale", "repropose", "missing"])
def test_file_fields_and_lifecycle(
    db: Session, client: TestClient, demo: tuple, file_case: tuple, action: str
) -> None:
    kind, p, sid, _, _ = file_case
    response = upload(client, demo, file_case)
    row = c.get(db, p, UUID(response.json()["messages"][1]["change_set"]["id"]))
    spec = {"function": row.items[0]["function"], "args": row.items[0]["args"]}
    for function in ("import_work_records",):
        with pytest.raises(c.ChangeSetError, match="上传框"):
            c.propose(
                db,
                p,
                sid,
                ProposeChanges.model_validate(
                    {
                        "items": [
                            {
                                "function": function,
                                "args": {"filename": "file", "content": "synthetic"},
                            }
                        ]
                    }
                ),
            )
        with pytest.raises(c.ChangeSetError):
            c.request_secret_input(db, p, sid, function, "content")
    with pytest.raises(c.ChangeSetError):
        c.save_secret(db, p, row.id, "content", "synthetic")
    with pytest.raises(c.ChangeSetError, match="不能重复"):
        c.propose(db, p, sid, ProposeChanges.model_validate({"items": [spec, spec]}))
    history = client.get(
        f"/api/v1/admin-assistant/sessions/{sid}/messages",
        params={"org_id": p.org_id},
        headers=demo[2]["admin"],
    )
    assert history.status_code == 200
    projected = json.dumps(files.model_context(db, store.get_session(db, p, sid)))
    assert (
        "content" not in projected and "synthetic-private-content-280" not in projected
    )
    assert "example.lin" not in projected and "员工自述" not in projected
    result = tools.call(db, p, [], "propose_changes", {"items": [spec]}, session_id=sid)
    assert "content" not in json.dumps(result)
    if action == "discard":
        c.discard(db, p, row.id)
    elif action == "stale":
        with pytest.raises(c.ChangeSetError, match="内容已变化"):
            c.apply(db, p, row.id, row.revision + 1)
    elif action == "repropose":
        c.propose(
            db,
            p,
            sid,
            ProposeChanges.model_validate(
                {"change_set_id": str(row.id), "items": [spec]}
            ),
        )
        db.commit()
    else:
        row.secrets = None
        db.add(row)
        db.commit()
    assert c.get(db, p, row.id).secrets is None
    with pytest.raises(c.ChangeSetError, match="文件已失效"):
        c.apply(db, p, row.id, c.get(db, p, row.id).revision)


def test_atomic_rollback(
    db: Session, client: TestClient, demo: tuple, file_case: tuple
) -> None:
    _, p, sid, _, _ = file_case
    before = state(db, p)
    response = upload(client, demo, file_case)
    row = c.get(db, p, UUID(response.json()["messages"][1]["change_set"]["id"]))
    saved = row.secrets
    specs = [{"function": i["function"], "args": i["args"]} for i in row.items]
    specs.append({"function": "complete_setup_step", "args": {"key": "missing"}})
    c.propose(
        db,
        p,
        sid,
        ProposeChanges.model_validate({"change_set_id": str(row.id), "items": specs}),
    )
    row.secrets = saved
    db.add(row)
    db.commit()
    with pytest.raises(c.ChangeSetError, match="均已回滚"):
        c.apply(db, p, row.id, row.revision)
    assert state(db, p) == before
    assert c.get(db, p, row.id).status == "draft" and row.secrets == saved


@pytest.mark.parametrize(
    "error",
    [
        InvalidImport(["第 1 个人：能力已变化"]),
        InvalidWorkRecords(
            [
                WorkRecordIssue(
                    row_number=2, column=None, level="error", message="缺少必填信息"
                )
            ]
        ),
    ],
)
def test_business_reason_is_plain_and_rollback(
    db: Session,
    client: TestClient,
    demo: tuple,
    file_case: tuple,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    _, p, sid, _, _ = file_case
    response = upload(client, demo, file_case)
    row = c.get(db, p, UUID(response.json()["messages"][1]["change_set"]["id"]))
    name = row.items[0]["function"]
    entry = registry.FUNCTIONS[name]
    before = state(db, p)

    def fail(*_args: Any, **_kwargs: Any) -> None:
        org = db.get(Organization, p.org_id)
        org.name = "should roll back"
        db.add(org)
        db.flush()
        raise error

    monkeypatch.setitem(registry.FUNCTIONS, name, replace(entry, handler=fail))
    with pytest.raises(c.ChangeSetError) as caught:
        c.apply(db, p, row.id, row.revision)
    detail = caught.value.detail
    assert (
        "均已回滚" in detail
        and "第 " in detail
        and "[" not in detail
        and "{" not in detail
    )
    assert db.get(Organization, p.org_id).name != "should roll back"
    assert state(db, p) == before


def test_summary_error_limit(db: Session, demo: tuple) -> None:
    preview = files.FileSummary(
        counts={},
        errors=[
            files.FileError(location=str(i), message="合成错误") for i in range(30)
        ],
    )
    payload = AdminTurnResponse(
        messages=[FilePreviewCard(kind="work_csv", preview=preview)]
    )
    session = store.create_session(db, principal(db, demo))
    messages.append_message(db, session, "assistant", payload.model_dump(mode="json"))
    assert len(files.model_context(db, session)[0]["preview"]["errors"]) == 20
