from collections.abc import Generator
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.authz.deps import get_org_principal
from app.authz.policies import can_view_employee, can_view_evidence_content
from app.authz.principal import Principal
from app.core.security import create_access_token, get_password_hash
from app.domains.audit.models import AuditEvent
from app.domains.bot import store, tools
from app.domains.capability.schemas import DictionaryDefinition
from app.domains.capability.service import save_version
from app.domains.change_request import employee
from app.domains.change_request.models import (
    ProfileChangeRequest,
    RequestOrigin,
    RequestStatus,
)
from app.domains.evidence.models import Evidence, EvidenceKind, Visibility
from app.domains.evidence.service import append_evidence
from app.domains.im import binding
from app.domains.im.models import ImBinding, ImSetting
from app.domains.inbox.models import InboxItem, InboxKind
from app.domains.manager_tools import get_team_overview
from app.domains.org.models import (
    Department,
    Employee,
    EmploymentStatus,
    Organization,
    OrgMembership,
    Role,
    RoleGrant,
)
from app.domains.org.roster import service, single
from app.domains.org.roster.schemas import ApplyImport
from app.domains.org.service import read_org_chart
from app.domains.review import direct
from app.domains.review.service import list_pending_reviews
from app.models import User
from tests.api.routes.test_mcp import call, initialize, token_for
from tests.utils.org import delete_test_organization


@pytest.fixture
def company(db: Session) -> Generator[tuple]:
    org = Organization(slug=str(uuid4()), name="合成离职权限企业")
    foreign = Organization(slug=str(uuid4()), name="合成外部企业")
    db.add_all([org, foreign])
    db.flush()
    parent = Department(org_id=org.id, name="研发中心")
    db.add(parent)
    db.flush()
    department = Department(
        org_id=org.id, name="平台部", parent_department_id=parent.id
    )
    other = Department(org_id=org.id, name="其他部门")
    db.add_all([department, other])
    db.flush()
    people, users, principals, auth = {}, {}, {}, {}
    for name in (
        "admin",
        "owner",
        "manager",
        "ancestor",
        "other",
        "foreign",
        "account_manager",
    ):
        user = User(
            username=f"{uuid4()}@example.com",
            hashed_password=get_password_hash("synthetic-password-2026"),
            full_name=name,
        )
        db.add(user)
        db.flush()
        users[name] = user
        organization = foreign if name == "foreign" else org
        person = None
        if name not in ("admin", "account_manager"):
            person = Employee(
                org_id=organization.id,
                employee_code=name,
                display_name=f"合成{name}",
                work_email=user.username,
                department_id=parent.id
                if name == "ancestor"
                else other.id
                if name == "other"
                else department.id
                if name != "foreign"
                else None,
            )
            db.add(person)
            db.flush()
            people[name] = person
        member = OrgMembership(
            org_id=organization.id,
            user_id=user.id,
            employee_id=person.id if person else None,
        )
        db.add(member)
        db.flush()
        if name in ("manager", "ancestor", "account_manager"):
            db.add(
                RoleGrant(
                    org_id=org.id,
                    membership_id=member.id,
                    role=Role.DEPT_MANAGER,
                    department_id=parent.id if name == "ancestor" else department.id,
                )
            )
        if name == "admin":
            db.add(
                RoleGrant(org_id=org.id, membership_id=member.id, role=Role.ORG_ADMIN)
            )
        db.flush()
        principals[name] = get_org_principal(organization.id, user, db)
        auth[name] = {
            "Authorization": f"Bearer {create_access_token(user.id, expires_delta=timedelta(minutes=30))}"
        }
    save_version(
        db,
        principals["admin"],
        expected_version=0,
        definitions=[
            DictionaryDefinition(
                key="synthetic_skill",
                name="合成能力",
                category="测试",
                description="合成权限测试",
                level_rubric={
                    "levels": [
                        {
                            "key": "routine",
                            "label": "独立完成",
                            "description": "合成等级",
                        }
                    ]
                },
            )
        ],
    )
    subject = people["owner"]
    direct.direct_edit(
        principals["admin"],
        db,
        subject.id,
        direct.DirectEditInput(
            items=[
                direct.DirectItemInput(
                    op="add",
                    capability_key="synthetic_skill",
                    statement="合成已发布判断",
                    level="routine",
                )
            ]
        ),
        via="web",
        client_name="Synthetic test",
    )
    for kind in (EvidenceKind.SELF_REPORT, EvidenceKind.MANAGER_REVIEW):
        append_evidence(
            db,
            org_id=org.id,
            subject_employee_id=subject.id,
            kind=kind,
            source="synthetic",
            source_ref=kind.value,
            title="合成受限标题",
            content={"text": "合成受限正文"},
            visibility=Visibility.RESTRICTED,
            provenance={},
        )
    db.commit()
    roster = single.current_roster(db, principals["admin"])
    try:
        yield f"/api/v1/orgs/{org.id}", people, principals, auth, users, roster
    finally:
        db.rollback()
        delete_test_organization(db, org.id)
        delete_test_organization(db, foreign.id)
        for user in users.values():
            db.delete(user)
        db.commit()


def depart(db: Session, company: tuple, *, commit: bool = True) -> None:
    _, people, principals, _, _, _ = company
    single.apply(
        db,
        principals["admin"],
        single.Offboard(employee_ids=[people["owner"].id]),
        commit=commit,
    )


@pytest.mark.parametrize("also_manager", [False, True])
def test_archived_http_scope_and_rehire(
    company: tuple, db: Session, client: TestClient, also_manager: bool
) -> None:
    base, people, principals, auth, users, roster = company
    subject = people["owner"]
    if also_manager:
        member = db.exec(
            select(OrgMembership).where(
                OrgMembership.org_id == subject.org_id,
                OrgMembership.user_id == users["admin"].id,
            )
        ).one()
        db.add(
            RoleGrant(
                org_id=subject.org_id,
                membership_id=member.id,
                role=Role.DEPT_MANAGER,
                department_id=subject.department_id,
            )
        )
        db.commit()
    depart(db, company)
    db.refresh(users["owner"])
    assert not users["owner"].is_active
    assert not can_view_employee(principals["owner"], subject)
    for role in ("owner", "manager", "ancestor", "other", "foreign"):
        expected = 403 if role == "owner" else 404
        for suffix in (
            "profile",
            "evidence",
            "profile-versions",
            "profile-versions/1",
            "change-requests",
        ):
            result = client.get(
                f"{base}/employees/{subject.id}/{suffix}", headers=auth[role]
            )
            assert result.status_code == expected, result.text
        listing = client.get(f"{base}/employees", headers=auth[role])
        if role not in ("owner", "foreign"):
            assert str(subject.id) not in {row["id"] for row in listing.json()["data"]}
    assert not can_view_evidence_content(
        get_org_principal(subject.org_id, users["admin"], db),
        subject_employee=subject,
        visibility=Visibility.RESTRICTED,
    )
    worker = Principal.system_worker(subject.org_id)
    assert can_view_evidence_content(
        worker, subject_employee=subject, visibility=Visibility.RESTRICTED
    )
    profile = client.get(
        f"{base}/employees/{subject.id}/profile", headers=auth["admin"]
    )
    assert profile.status_code == 200
    item = profile.json()["items"][0]
    assert item["capability"]["name"] == "合成能力"
    assert item["claim"]["level"] == "routine"
    assert item["status"] == "confirmed"
    records = db.exec(
        select(Evidence).where(
            Evidence.org_id == subject.org_id,
            Evidence.subject_employee_id == subject.id,
        )
    ).all()
    for row in records:
        detail = client.get(f"{base}/evidence/{row.id}", headers=auth["admin"])
        assert detail.status_code == 200
        assert detail.json()["redacted"] and detail.json()["content"] is None
        assert (
            client.get(f"{base}/evidence/{row.id}", headers=auth["manager"]).status_code
            == 404
        )
    body = {
        "base_revision": 1,
        "items": [
            {
                "op": "modify",
                "capability_key": "synthetic_skill",
                "statement": "合成更改",
                "level": "routine",
            }
        ],
    }
    assert (
        client.post(
            f"{base}/employees/{subject.id}/profile/direct-edits",
            headers=auth["admin"],
            json=body,
        ).status_code
        == 404
    )
    assert client.get("/api/v1/users/me", headers=auth["owner"]).status_code == 403
    preview = service.create_import(db, principals["admin"], roster, "合成恢复在职")
    service.apply_import(
        db,
        principals["admin"],
        preview.id,
        ApplyImport(base_revision=preview.base_revision),
    )
    assert (
        client.get(
            f"{base}/employees/{subject.id}/profile", headers=auth["manager"]
        ).status_code
        == 200
    )


@pytest.mark.parametrize("via", ["single", "import", "rollback"])
def test_departure_withdraws_atomically_without_changing_evidence(
    company: tuple, db: Session, via: str
) -> None:
    _, people, principals, _, users, roster = company
    subject = people["owner"]
    requests = []
    for status in (
        RequestStatus.DRAFT,
        RequestStatus.PENDING_OWNER,
        RequestStatus.PENDING_REVIEW,
        RequestStatus.APPROVED,
    ):
        request = ProfileChangeRequest(
            org_id=subject.org_id,
            subject_employee_id=subject.id,
            origin=RequestOrigin.INITIAL_SYNC
            if status == RequestStatus.PENDING_OWNER
            else RequestOrigin.MANUAL,
            status=status,
            author={},
        )
        db.add(request)
        db.flush()
        requests.append(request)
        if status != RequestStatus.APPROVED:
            db.add(
                InboxItem(
                    org_id=subject.org_id,
                    recipient_user_id=users["manager"].id,
                    request_id=request.id,
                    kind=InboxKind.REVIEW_REQUESTED,
                    message="合成待审提醒",
                )
            )
    unrelated = ProfileChangeRequest(
        org_id=subject.org_id,
        subject_employee_id=people["other"].id,
        origin=RequestOrigin.MANUAL,
        status=RequestStatus.DRAFT,
        author={},
    )
    db.add(unrelated)
    db.commit()
    before = {
        e.id: e.model_dump()
        for e in db.exec(
            select(Evidence).where(Evidence.org_id == subject.org_id)
        ).all()
    }
    if via == "import":
        departing_roster = roster.model_copy(
            update={
                "rows": [
                    row
                    for row in roster.rows
                    if row.employee_code != subject.employee_code
                ]
            }
        )
        preview = service.create_import(
            db, principals["admin"], departing_roster, "合成离职名单"
        )
        service.apply_import(
            db,
            principals["admin"],
            preview.id,
            ApplyImport(
                base_revision=preview.base_revision, confirm_mass_deactivation=True
            ),
        )
    else:
        depart(db, company, commit=via != "rollback")
    for index, request in enumerate(requests):
        db.refresh(request)
        assert request.status == (
            RequestStatus.APPROVED if index == 3 else RequestStatus.WITHDRAWN
        )
        assert request.revision == (3 if index == 1 else 1 if index == 3 else 2)
        audits = db.exec(
            select(AuditEvent).where(
                AuditEvent.org_id == subject.org_id,
                AuditEvent.target_id == str(request.id),
                AuditEvent.action == "change_request.withdrawn",
            )
        ).all()
        assert len(audits) == (0 if index == 3 else 1)
        inbox = db.exec(
            select(InboxItem).where(
                InboxItem.org_id == subject.org_id,
                InboxItem.request_id == request.id,
                InboxItem.kind == InboxKind.REVIEW_REQUESTED,
            )
        ).all()
        assert all(i.done_at and i.read_at for i in inbox)
    assert {
        e.id: e.model_dump()
        for e in db.exec(
            select(Evidence).where(Evidence.org_id == subject.org_id)
        ).all()
    } == before
    assert unrelated.status == RequestStatus.DRAFT
    assert not list_pending_reviews(principals["manager"], db).data
    if via == "rollback":
        db.rollback()
        db.refresh(subject)
        db.refresh(users["owner"])
        assert (
            subject.employment_status == EmploymentStatus.ACTIVE
            and users["owner"].is_active
        )
        for request, status in zip(
            requests,
            (
                RequestStatus.DRAFT,
                RequestStatus.PENDING_OWNER,
                RequestStatus.PENDING_REVIEW,
                RequestStatus.APPROVED,
            ),
            strict=True,
        ):
            db.refresh(request)
            assert request.status == status and request.revision == 1
            assert not db.exec(
                select(AuditEvent).where(
                    AuditEvent.org_id == subject.org_id,
                    AuditEvent.target_id == str(request.id),
                    AuditEvent.action == "change_request.withdrawn",
                )
            ).all()
    else:
        preview = service.create_import(
            db,
            principals["admin"],
            roster,
            "合成恢复名单",
        )
        service.apply_import(
            db,
            principals["admin"],
            preview.id,
            ApplyImport(base_revision=preview.base_revision),
        )
        for request in requests[:3]:
            db.refresh(request)
            assert request.status == RequestStatus.WITHDRAWN


@pytest.mark.parametrize("channel", ["web", "feishu"])
def test_assistant_team_and_employee_read_paths(
    company: tuple, db: Session, channel: str
) -> None:
    _, people, principals, _, _, _ = company
    p = principals["manager"]
    session = store.create_session(db, p, channel, "synthetic-chat", "synthetic-bot")
    subject = people["owner"]
    assert tools.team_call(
        db, p, [], "get_member_profile", {"employee_id": str(subject.id)}
    )
    count = get_team_overview(p, db).employee_count
    depart(db, company)
    assert get_team_overview(p, db).employee_count == count - 1
    assert str(subject.id) not in str(
        tools.call(db, p, session, [], "list_team_members", {})
    )
    with pytest.raises(HTTPException) as denied:
        tools.team_call(
            db, p, [], "get_member_profile", {"employee_id": str(subject.id)}
        )
    assert denied.value.status_code == 404
    with pytest.raises(HTTPException) as own_denied:
        employee.my_profile(principals["owner"], db)
    assert own_denied.value.status_code == 404


def test_feishu_binding_and_mcp_tokens_stop_working(
    company: tuple, db: Session, client: TestClient
) -> None:
    _, people, principals, _, _, _ = company
    p = principals["owner"]
    db.add(
        ImSetting(
            org_id=p.org_id,
            platform="feishu",
            enabled=True,
            bot_identity="synthetic-bot",
            credentials="synthetic",
            updated_by_user_id=p.user_id,
        )
    )
    db.add(
        ImBinding(
            org_id=p.org_id,
            employee_id=p.employee_id,
            platform="feishu",
            external_user_id="synthetic-user",
            external_chat_id="synthetic-chat",
            bot_identity="synthetic-bot",
        )
    )
    db.commit()
    assert binding.current_binding(
        db, p.org_id, "feishu", "synthetic-user", "synthetic-chat", "synthetic-bot"
    )
    owner_headers, _ = token_for(db, people["owner"])
    manager_headers, _ = token_for(db, people["manager"])
    owner_headers = initialize(client, owner_headers)
    manager_headers = initialize(client, manager_headers)
    depart(db, company)
    assert (
        binding.current_binding(
            db, p.org_id, "feishu", "synthetic-user", "synthetic-chat", "synthetic-bot"
        )
        is None
    )
    overview = call(client, manager_headers, "get_team_overview", {})
    assert not overview.get("isError")
    assert overview["structuredContent"]["employee_count"] == 1
    response = client.post(
        "/mcp",
        headers=owner_headers,
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "get_my_profile", "arguments": {}},
        },
    )
    assert response.status_code == 401


def test_org_chart_preserves_account_managers_and_hides_departed_managers(
    company: tuple, db: Session
) -> None:
    _, people, principals, _, users, _ = company
    manager = people["manager"]
    manager.employment_status = EmploymentStatus.INACTIVE
    users["manager"].is_active = False
    db.add_all([manager, users["manager"]])
    db.commit()
    for role in ("ancestor", "admin"):
        chart = read_org_chart(db, principals[role])
        managers = [m for d in chart.departments for m in d.managers]
        assert any(
            m.employee_id is None
            and m.display_name == users["account_manager"].username
            for m in managers
        )
        assert any(m.employee_id == manager.id for m in managers) == (role == "admin")
