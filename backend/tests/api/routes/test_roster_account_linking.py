from io import BytesIO
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlmodel import Session, col, select

from app.core.security import get_password_hash
from app.domains.admin_assistant import change_set, store
from app.domains.admin_assistant.schemas import ProposeChanges
from app.domains.audit.models import AuditEvent
from app.domains.change_request.models import (
    ProfileChangeRequest,
    RequestOrigin,
    RequestStatus,
)
from app.domains.org.models import Employee, OrgMembership, ReportingLine, RoleGrant
from app.domains.org.roster import service
from app.models import User
from tests.admin_assistant.test_personnel import principal
from tests.api.routes.test_roster import apply, employees, upload
from tests.api.routes.test_roster import client as client
from tests.api.routes.test_setup_steps import SetupTenant
from tests.api.routes.test_setup_steps import tenant as tenant

PASSWORD = "synthetic-owner-password"


@pytest.fixture
def admin(db: Session, tenant: SetupTenant) -> User:
    user = db.get_one(User, tenant.admin_id)
    user.username = user.email = f"owner-{uuid4()}@example.com"
    user.hashed_password = get_password_hash(PASSWORD)
    db.add(user)
    db.commit()
    return user


def membership(db: Session, tenant: SetupTenant, user: User) -> OrgMembership:
    return db.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == tenant.org_id, OrgMembership.user_id == user.id
        )
    ).one()


def csv(email: str, *, extra: str = "") -> bytes:
    return f"工号,姓名,部门,邮箱,上级工号\nCEO,合成负责人,总经办,{email},\n{extra}".encode()


@pytest.mark.parametrize("via", ["import", "single", "material", "assistant_single"])
@pytest.mark.parametrize("identifier", ["email", "username"])
def test_admin_can_add_self_to_roster(
    db: Session,
    tenant: SetupTenant,
    client: TestClient,
    admin: User,
    via: str,
    identifier: str,
) -> None:
    email = admin.username
    if identifier == "username":
        admin.email = f"contact-{uuid4()}@example.com"
        db.add(admin)
        db.commit()
    db.refresh(admin)
    original = admin.model_dump()
    grants = list(
        db.exec(select(RoleGrant).where(RoleGrant.org_id == tenant.org_id)).all()
    )
    if via in {"material", "assistant_single"}:
        p = principal(db, tenant)
        draft = change_set.propose(
            db,
            p,
            store.create_session(db, p).id,
            ProposeChanges.model_validate(
                {
                    "items": [
                        {
                            "function": "draft_roster_from_material",
                            "args": {
                                "materials": [
                                    {"label": "合成名单", "attachment_id": None}
                                ],
                                "rows": [
                                    {
                                        "action": "onboard",
                                        "employee_code": "CEO",
                                        "display_name": "合成负责人",
                                        "department": "总经办",
                                        "email": email,
                                    }
                                ],
                            },
                        }
                    ],
                }
            ),
        )
        if via == "assistant_single":
            draft = change_set.propose(
                db,
                p,
                store.create_session(db, p).id,
                ProposeChanges.model_validate(
                    {
                        "items": [
                            {
                                "function": "onboard_employee",
                                "args": {
                                    "employee_code": "CEO",
                                    "display_name": "合成负责人",
                                    "department": "总经办",
                                    "email": email,
                                },
                            }
                        ],
                    }
                ),
            )
        assert f"将关联现有账号 {email}，不重置密码" in draft.items[0]["impact"]["text"]
        assert change_set.apply(db, p, draft.id, draft.revision).status == "applied"
        db.commit()
    else:
        preview = (
            upload(client, tenant, csv(email))
            if via == "import"
            else client.post(
                f"{tenant.url}/roster/single-change",
                json={
                    "kind": "onboard",
                    "employee_code": "CEO",
                    "display_name": "合成负责人",
                    "department": "总经办",
                    "email": email,
                },
            ).json()
        )
        assert preview["can_apply"], preview["issues"]
        assert any(
            f"将关联现有账号 {email}，不重置密码" in i["message"]
            for i in preview["warnings"]
        )
        assert apply(client, tenant, preview).status_code == 200
    employee = employees(db, tenant)["CEO"]
    assert membership(db, tenant, admin).employee_id == employee.id
    db.refresh(admin)
    assert admin.model_dump() == original
    assert (
        list(db.exec(select(RoleGrant).where(RoleGrant.org_id == tenant.org_id)).all())
        == grants
    )
    event = db.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == tenant.org_id,
            AuditEvent.action == "employee.account_linked",
        )
    ).one()
    assert event.target_id == str(admin.id)
    assert event.details["user_id"] == str(admin.id)
    assert event.details["employee_id"] == str(employee.id)
    login = client.post(
        "/api/v1/login/access-token", data={"username": email, "password": PASSWORD}
    )
    assert login.status_code == 200
    auth = {"Authorization": f"Bearer {login.json()['access_token']}"}
    me = client.get("/api/v1/me/memberships", headers=auth).json()[0]
    assert me["employee_id"] == str(employee.id)
    assert set(me["roles"]) == {"org_admin", "employee"}
    assert (
        client.get(
            f"{tenant.url}/employees/{employee.id}/profile", headers=auth
        ).status_code
        == 200
    )
    # The linked owner can head the five departments without gaining self-write rights.
    preview = upload(
        client,
        tenant,
        csv(
            email,
            extra="".join(
                f"M{i},合成经理{i},部门{i},m{i}-{uuid4()}@example.com,CEO\n"
                for i in range(5)
            ),
        ),
    )
    assert apply(client, tenant, preview).status_code == 200
    assert (
        len(
            db.exec(
                select(ReportingLine).where(
                    ReportingLine.org_id == tenant.org_id,
                    ReportingLine.manager_employee_id == employee.id,
                    col(ReportingLine.valid_to).is_(None),
                )
            ).all()
        )
        == 5
    )
    edit = {
        "base_revision": None,
        "items": [{"op": "add", "capability_key": "unused", "statement": "合成陈述"}],
    }
    for suffix in ("", "/preview"):
        result = client.post(
            f"{tenant.url}/employees/{employee.id}/profile/direct-edits{suffix}",
            json=edit,
        )
        assert result.status_code == 403
    request = ProfileChangeRequest(
        org_id=tenant.org_id,
        subject_employee_id=employee.id,
        origin=RequestOrigin.MANUAL,
        status=RequestStatus.PENDING_REVIEW,
        author={"user_id": str(admin.id)},
    )
    db.add(request)
    db.commit()
    assert client.get(f"{tenant.url}/reviews/{request.id}").status_code == 403
    assert (
        client.post(
            f"{tenant.url}/reviews/{request.id}/decisions",
            json={
                "revision": request.revision,
                "decisions": [{"item_id": str(uuid4()), "decision": "approved"}],
            },
        ).status_code
        == 403
    )
    assert client.get(f"{tenant.url}/me/inbox").status_code == 200
    assert (
        client.get(
            f"{tenant.url}/employees/{employee.id}/profile",
            headers=tenant.auth["foreign"],
        ).status_code
        == 404
    )


@pytest.mark.parametrize("when", ["before", "after"])
@pytest.mark.parametrize(
    "unsafe", ["linked", "shared", "foreign", "superuser", "inactive"]
)
def test_unsafe_accounts_blocked_and_revalidated(
    db: Session,
    tenant: SetupTenant,
    client: TestClient,
    unsafe: str,
    when: str,
) -> None:
    target = User(username=f"target-{uuid4()}@example.com", hashed_password="unused")
    db.add(target)
    db.flush()
    member = OrgMembership(org_id=tenant.org_id, user_id=target.id)
    db.add(member)
    db.commit()
    preview = upload(client, tenant, csv(target.username)) if when == "after" else None
    if preview:
        assert preview["can_apply"]
    if unsafe == "linked":
        old = Employee(
            org_id=tenant.org_id, employee_code="OLD", display_name="其他员工"
        )
        db.add(old)
        db.flush()
        member.employee_id = old.id
        db.add(member)
    elif unsafe == "shared":
        db.add(OrgMembership(org_id=tenant.foreign_org_id, user_id=target.id))
    elif unsafe == "foreign":
        db.delete(member)
        db.add(OrgMembership(org_id=tenant.foreign_org_id, user_id=target.id))
    elif unsafe == "superuser":
        target.is_superuser = True
        db.add(target)
    else:
        target.is_active = False
        db.add(target)
    db.commit()
    preview = preview or upload(client, tenant, csv(target.username))
    if when == "before":
        assert not preview["can_apply"]
    assert apply(client, tenant, preview).status_code == 422
    assert "CEO" not in employees(db, tenant)
    assert not db.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == tenant.org_id,
            AuditEvent.action == "employee.account_linked",
        )
    ).all()


@pytest.mark.parametrize(
    "unsafe",
    ["existing_employee", "duplicate_email", "duplicate_alias", "ambiguous_owner"],
)
def test_email_changes_and_duplicate_owners_cannot_link(
    db: Session, tenant: SetupTenant, client: TestClient, admin: User, unsafe: str
) -> None:
    extra = ""
    if unsafe == "existing_employee":
        db.add(
            Employee(
                org_id=tenant.org_id,
                employee_code="CEO",
                display_name="已有员工",
                work_email="old@example.com",
            )
        )
    elif unsafe == "ambiguous_owner":
        db.add(User(username=str(uuid4()), email=admin.email, hashed_password="unused"))
        admin.email = None
        db.add(admin)
    else:
        if unsafe == "duplicate_alias":
            admin.email = f"alias-{uuid4()}@example.com"
            db.add(admin)
        extra = f"E2,另一员工,研发,{admin.email},\n"
    db.commit()
    preview = upload(client, tenant, csv(admin.username, extra=extra))
    assert not preview["can_apply"]
    assert apply(client, tenant, preview).status_code == 422
    assert membership(db, tenant, admin).employee_id is None


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("regenerate", [False, True])
def test_issuing_accounts_preserves_linked_owner(
    db: Session,
    tenant: SetupTenant,
    client: TestClient,
    admin: User,
    explicit: bool,
    regenerate: bool,
) -> None:
    preview = upload(
        client,
        tenant,
        csv(admin.username, extra=f"E2,新员工,研发,new-{uuid4()}@example.com,CEO\n"),
    )
    assert apply(client, tenant, preview).status_code == 200
    db.refresh(admin)
    original = admin.model_dump()
    people = employees(db, tenant)
    response = client.post(
        f"{tenant.url}/accounts/issue",
        json={
            "employee_ids": [str(e.id) for e in people.values()] if explicit else None,
            "regenerate_pending": regenerate,
        },
    )
    assert response.status_code == 200, response.text
    rows = list(load_workbook(BytesIO(response.content)).active.values)[1:]
    owner = next(r for r in rows if r[0] == admin.username)
    assert owner[4] is None and owner[5] == "已有账号"
    assert next(r for r in rows if r[0] != admin.username)[4]
    db.refresh(admin)
    assert admin.model_dump() == original
    assert (
        client.post(
            "/api/v1/login/access-token",
            data={"username": admin.username, "password": PASSWORD},
        ).status_code
        == 200
    )
    assert not db.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == tenant.org_id,
            AuditEvent.action == "account.issued",
            AuditEvent.target_id == str(admin.id),
        )
    ).all()


def test_link_rolls_back_with_failed_roster(
    db: Session,
    tenant: SetupTenant,
    client: TestClient,
    admin: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preview = upload(client, tenant, csv(admin.username))

    def fail(*_args: object, **_kwargs: object) -> None:
        raise service.RosterError(422, "合成失败")

    monkeypatch.setattr(service, "auto_link_identities", fail)
    assert apply(client, tenant, preview).status_code == 422
    db.expire_all()
    assert membership(db, tenant, admin).employee_id is None
    assert not employees(db, tenant)
    assert not db.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == tenant.org_id,
            AuditEvent.action == "employee.account_linked",
        )
    ).all()
