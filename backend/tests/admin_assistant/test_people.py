import json
from base64 import b64decode
from io import BytesIO
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlmodel import Session, select

from app.authz.deps import get_org_principal
from app.domains.account import service as accounts
from app.domains.account.schemas import IssueAccounts
from app.domains.admin_assistant import change_set as c
from app.domains.admin_assistant import core, registry, store
from app.domains.admin_assistant.models import AdminChangeSet
from app.domains.admin_assistant.schemas import AdminTurnInput, ProposeChanges
from app.domains.audit.models import AuditEvent
from app.domains.bot import store as messages
from app.domains.change_request.models import ItemDecision
from app.domains.model.pi_client import TurnResult
from app.domains.org.models import Employee, OrgMembership, Role, RoleGrant
from app.domains.review import appeal
from app.domains.review import service as reviews
from app.models import User
from tests.admin_assistant.test_registry import principal
from tests.admin_assistant.test_secure_changes import draft
from tests.api.routes.test_reviews import manager, pending, values
from tests.domains.test_bot_core import enable


def user_ids(db: Session, demo: tuple) -> list[UUID]:
    p = principal(db, demo)
    employees = [
        Employee(
            org_id=p.org_id,
            employee_code=str(uuid4()),
            display_name="合成员工",
            work_email=f"{uuid4()}@example.com",
        )
        for _ in range(2)
    ]
    db.add_all(employees)
    db.commit()
    accounts.issue_accounts(
        db, p, IssueAccounts(employee_ids=[e.id for e in employees])
    )
    db.commit()
    return list(
        db.exec(
            select(OrgMembership.user_id).where(
                OrgMembership.org_id == p.org_id,
                OrgMembership.employee_id.in_([e.id for e in employees]),
            )
        ).all()
    )


def test_password_once_and_no_disclosure(
    db: Session, demo: tuple, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = principal(db, demo)
    uid = user_ids(db, demo)[0]
    canary = "Synthetic-password-canary-281!"
    generated = []

    def generate() -> tuple[str, str]:
        generated.append(canary)
        return canary, "synthetic-hash"

    monkeypatch.setattr(accounts, "generate_initial_password", generate)
    row = draft(db, demo, "reset_password", {"user_id": str(uid)})
    sid, rid, rev = row.session_id, row.id, row.revision
    url = f"{demo[0]}/admin-assistant/change-sets/{rid}"
    headers = demo[2]["admin"]
    first = client.post(url + "/apply", headers=headers, json={"revision": rev})
    assert first.json()["status"] == "draft" and first.json()["one_time"] == []
    assert generated == []
    second = client.post(url + "/apply", headers=headers, json={"revision": rev})
    assert second.status_code == 200, second.text
    assert second.headers["cache-control"] == "no-store"
    assert second.json()["one_time"][0]["initial_password"] == canary
    assert generated == [canary]
    assert (
        client.post(url + "/apply", headers=headers, json={"revision": rev}).status_code
        == 409
    )
    assert client.post(url + "/undo", headers=headers).status_code == 409
    enable(db, p.org_id)

    def reply(_config: object, **kwargs: object) -> TurnResult:
        assert canary not in str(kwargs)
        return TurnResult(
            text="已处理", stop_reason="stop", model_calls=1, tool_calls=0
        )

    monkeypatch.setattr(core.pi_client, "run_turn", reply)
    core.turn(db, p, sid, AdminTurnInput(text="查看处理结果", page="/admin"))
    for endpoint in (
        url,
        f"/api/v1/admin-assistant/sessions/{sid}/messages?org_id={p.org_id}",
        f"{demo[0]}/audit-events",
        f"{demo[0]}/admin-assistant/change-sets?function=reset_password&status=draft",
    ):
        response = client.get(endpoint, headers=headers)
        assert response.status_code == 200 and canary not in response.text
        assert '"one_time"' not in response.text
    db.expire_all()
    assert canary not in json.dumps(db.get(AdminChangeSet, rid).model_dump(mode="json"))
    assert canary not in str(messages.messages_for(db, store.get_session(db, p, sid)))
    assert canary not in str(
        [
            e.details
            for e in db.exec(
                select(AuditEvent).where(AuditEvent.org_id == p.org_id)
            ).all()
        ]
    )


def test_account_file_and_remaining_actions(db: Session, demo: tuple) -> None:
    p = principal(db, demo)
    employee = Employee(
        org_id=p.org_id,
        employee_code="NEW281",
        display_name="合成员工",
        work_email="new281@example.com",
    )
    db.add(employee)
    db.commit()
    row = draft(db, demo, "issue_accounts", {"employee_ids": [str(employee.id)]})
    result = c.apply(db, p, row.id, row.revision)
    output = result.one_time[0]
    assert output.type == "accounts_file" and output.count == 1
    sheet = load_workbook(BytesIO(b64decode(output.content_base64))).active
    assert list(sheet.values)[1][0:2] == (employee.work_email, employee.display_name)
    assert "one_time" not in c.public(c.get(db, p, row.id)).model_dump()
    with pytest.raises(c.ChangeSetError, match="不能撤销"):
        c.undo(db, p, row.id)
    uid = db.exec(
        select(OrgMembership.user_id).where(
            OrgMembership.org_id == p.org_id, OrgMembership.employee_id == employee.id
        )
    ).one()
    for function, args in [
        ("disable_accounts", {"user_ids": [str(uid)]}),
        ("enable_account", {"user_id": str(uid)}),
        (
            "grant_role",
            {
                "user_id": str(uid),
                "role": "dept_manager",
                "department_id": str(demo[1]["E02"].department_id),
            },
        ),
        ("grant_role", {"user_id": str(uid), "role": "org_admin"}),
    ]:
        row = draft(db, demo, function, args)
        first = c.apply(db, p, row.id, row.revision)
        if args.get("role") == "org_admin":
            assert first.status == "draft"
            first = c.apply(db, p, row.id, row.revision)
        assert first.status == "applied" and not first.items[0].undoable
        assert str(uid) not in first.items[0].impact.text
        with pytest.raises(c.ChangeSetError, match="不能撤销"):
            c.undo(db, p, row.id)
    grant = next(
        r
        for r in accounts.list_roles(db, p)
        if r.user_id == uid and r.role == Role.DEPT_MANAGER
    )
    row = draft(db, demo, "revoke_role", {"grant_id": str(grant.id)})
    assert "部门经理" in c.public(row).items[0].impact.text
    assert c.apply(db, p, row.id, row.revision).status == "applied"
    with pytest.raises(c.ChangeSetError, match="不能撤销"):
        c.undo(db, p, row.id)


@pytest.mark.parametrize("split", [False, True])
def test_bulk_disable_requires_two_clicks(
    db: Session, demo: tuple, split: bool
) -> None:
    p = principal(db, demo)
    ids = [str(uid) for uid in user_ids(db, demo)]
    body = {
        "items": [
            {"function": "disable_accounts", "args": {"user_ids": group}}
            for group in ([ids] if not split else [[uid] for uid in ids])
        ]
    }
    row = c.propose(
        db, p, store.create_session(db, p).id, ProposeChanges.model_validate(body)
    )
    db.commit()
    assert c.apply(db, p, row.id, row.revision).status == "draft"
    assert all(accounts.member(db, p.org_id, UUID(uid))[1].is_active for uid in ids)
    assert c.apply(db, p, row.id, row.revision).status == "applied"
    assert all(not accounts.member(db, p.org_id, UUID(uid))[1].is_active for uid in ids)


@pytest.mark.parametrize("last_admin", [False, True])
def test_protections_roll_back_whole_set(
    db: Session, demo: tuple, last_admin: bool
) -> None:
    p = principal(db, demo)
    uid = user_ids(db, demo)[0]
    grant = next(g for g in accounts.list_roles(db, p) if g.user_id == p.user_id)
    user = User(username=str(uuid4()), hashed_password="synthetic")
    db.add(user)
    db.flush()
    membership = OrgMembership(org_id=p.org_id, user_id=user.id)
    db.add(membership)
    db.flush()
    for existing in db.exec(
        select(RoleGrant).where(
            RoleGrant.org_id == p.org_id, RoleGrant.role == Role.ORG_ADMIN
        )
    ).all():
        db.delete(existing)
    grant = RoleGrant(org_id=p.org_id, membership_id=membership.id, role=Role.ORG_ADMIN)
    db.add(grant)
    db.commit()
    p = get_org_principal(p.org_id, user, db)
    specs = [
        {"function": "disable_accounts", "args": {"user_ids": [str(uid)]}},
        {"function": "revoke_role", "args": {"grant_id": str(grant.id)}}
        if last_admin
        else {"function": "disable_accounts", "args": {"user_ids": [str(p.user_id)]}},
    ]
    row = c.propose(
        db,
        p,
        store.create_session(db, p).id,
        ProposeChanges.model_validate({"items": specs}),
    )
    db.commit()
    if not last_admin:
        assert c.apply(db, p, row.id, row.revision).status == "draft"
    with pytest.raises(
        c.ChangeSetError,
        match="最后一名" if last_admin else "不能重置自己的密码或停用自己",
    ):
        c.apply(db, p, row.id, row.revision)
    assert accounts.member(db, p.org_id, uid)[1].is_active
    assert c.get(db, p, row.id).status == "draft"


@pytest.mark.parametrize(
    "function,key",
    [
        ("issue_accounts", "employee_ids"),
        ("reset_password", "user_id"),
        ("disable_accounts", "user_ids"),
        ("enable_account", "user_id"),
        ("grant_role", "user_id"),
        ("revoke_role", "grant_id"),
        ("resolve_appeal", "appeal_id"),
    ],
)
def test_foreign_targets_hidden(
    db: Session, demo: tuple, function: str, key: str
) -> None:
    import jwt

    target = jwt.decode(
        demo[2]["foreign"]["Authorization"].split()[1],
        options={"verify_signature": False},
    )["sub"]
    foreign = db.exec(
        select(OrgMembership).where(OrgMembership.user_id == UUID(target))
    ).one()
    if key == "employee_ids":
        employee = Employee(org_id=foreign.org_id, display_name="合成外部员工")
        db.add(employee)
        db.commit()
        target = str(employee.id)
    elif key == "grant_id":
        grant = RoleGrant(
            org_id=foreign.org_id, membership_id=foreign.id, role=Role.ORG_ADMIN
        )
        db.add(grant)
        db.commit()
        target = str(grant.id)
    args = {key: [str(target)] if key.endswith("ids") else str(target)}
    if function == "grant_role":
        args["role"] = "org_admin"
    if function == "resolve_appeal":
        args.update(status="upheld", reason="合成说明")
    with pytest.raises(HTTPException) as error:
        draft(db, demo, function, args)
    assert error.value.status_code == 404


@pytest.mark.parametrize("kind", ["account", "role", "appeal"])
def test_state_changes_stale(db: Session, demo: tuple, kind: str) -> None:
    p = principal(db, demo)
    uid = user_ids(db, demo)[0]
    if kind == "appeal":
        owner, request = pending(db, demo)
        reviews.decide(
            manager(db, demo),
            db,
            request.id,
            request.revision,
            values(request, ItemDecision.REJECTED),
        )
        row = appeal.appeal_decision(
            owner, db, request.id, request.items[0].id, "合成申诉"
        )
        db.commit()
        from dataclasses import replace
        from uuid import uuid4

        with pytest.raises(HTTPException) as hidden:
            registry.FUNCTIONS["resolve_appeal"].snapshot(
                db,
                replace(p, org_id=uuid4()),
                registry.FUNCTIONS["resolve_appeal"].input_model.model_validate(
                    {"appeal_id": str(row.id), "status": "upheld", "reason": "合成处理"}
                ),
            )
        assert hidden.value.status_code == 404
        change = draft(
            db,
            demo,
            "resolve_appeal",
            {"appeal_id": str(row.id), "status": "upheld", "reason": "合成处理"},
        )
        other = draft(
            db,
            demo,
            "resolve_appeal",
            {"appeal_id": str(row.id), "status": "reopened", "reason": "合成处理"},
        )
        assert c.apply(db, p, change.id, change.revision).status == "applied"
        with pytest.raises(c.ChangeSetError, match="内容已变化"):
            c.apply(db, p, other.id, other.revision)
        with pytest.raises(c.ChangeSetError, match="不能撤销"):
            c.undo(db, p, change.id)
        return
    change = draft(db, demo, "reset_password", {"user_id": str(uid)})
    if kind == "account":
        accounts.change_account(db, p, uid, "disabled")
    else:
        membership, _ = accounts.member(db, p.org_id, uid)
        db.add(
            RoleGrant(org_id=p.org_id, membership_id=membership.id, role=Role.ORG_ADMIN)
        )
        db.commit()
    with pytest.raises(c.ChangeSetError, match="内容已变化"):
        c.apply(db, p, change.id, change.revision)
    assert c.get(db, p, change.id).status == "stale"
