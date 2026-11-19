from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlmodel import Session, select

from app.core.security import create_access_token
from app.domains.base import utc_now
from app.domains.capacity.models import (
    CapacityAssignment,
    CapacityProject,
    CapacityProposal,
    CapacityProposalLine,
)
from app.domains.change_request.models import ProfileChangeRequest, RequestOrigin
from app.domains.evidence.models import EvidenceKind, Visibility
from app.domains.evidence.service import append_evidence
from app.domains.org.models import (
    Department,
    Employee,
    Organization,
    OrgMembership,
    RoleGrant,
)
from app.domains.profile.models import ProfileVersion
from tests.api.routes.test_m6_capacity_assignment import case as case
from tests.utils.migrations import migrate

FEEDBACK = "合成反馈：员工完成了接口联调和交付验收，并整理了复核记录。"
BODY = {"text": FEEDBACK, "capability_keys": ["synthetic", "support"]}
LIST = "/capacity/assignments/feedback"


@pytest.fixture
def org_api(case: tuple, client: TestClient, db: Session) -> Callable[..., Any]:
    _, _, demand, _, users, org = case

    def send(
        path: str,
        role: str,
        body: dict | None = None,
        *,
        status: int = 200,
        method: str | None = None,
    ) -> Any:
        token = create_access_token(users[role].id, expires_delta=timedelta(hours=1))
        res = client.request(
            method or ("GET" if body is None else "POST"),
            f"/api/v1/orgs/{org.id}{path}",
            headers={"Authorization": f"Bearer {token}"},
            json=body,
        )
        assert res.status_code == status, res.text
        return res.json()

    send(
        "/capabilities/versions",
        "admin",
        {
            "expected_version": 0,
            "definitions": [
                {
                    "key": key,
                    "name": key,
                    "category": key,
                    "description": "合成能力",
                    "level_rubric": {
                        "levels": [
                            {
                                "key": "routine",
                                "label": "独立完成",
                                "description": "合成等级",
                            }
                        ]
                    },
                }
                for key in ("synthetic", "support", "optional")
            ],
        },
    )
    demand.minimum_level = "routine"
    demand.extra_capabilities = [
        {"key": "support", "required": True, "minimum_level": None},
        {"key": "optional", "required": False, "minimum_level": None},
    ]
    db.commit()
    return send


def complete(case: tuple, code: str = "w") -> str:
    api, create, *_ = case
    assignment_id = create(code=code)["id"]
    api(f"/assignments/{assignment_id}", "bm", {"status": "done"}, method="PATCH")
    return assignment_id


def owner_request(api: Callable[..., Any], request_id: str) -> dict:
    return next(
        r for r in api("/me/requests?status=all", "w")["data"] if r["id"] == request_id
    )


def decide(
    api: Callable[..., Any], request: dict, decision: str = "approved", role: str = "cm"
) -> dict:
    return api(
        f"/reviews/{request['id']}/decisions",
        role,
        {
            "revision": request["revision"],
            "decisions": [
                {"item_id": item["id"], "decision": decision, "reason": "请补充情况"}
                for item in request["items"]
                if item["decision"] is None
            ],
        },
    )


def test_permissions_and_concurrent_feedback(
    case: tuple, org_api: Callable[..., Any], db: Session
) -> None:
    api, create, demand, people, _, _ = case
    assignment_id = create()["id"]
    path = f"/assignments/{assignment_id}/feedback"
    api(path, "bm", BODY, status=409)
    api(f"/assignments/{assignment_id}", "bm", {"status": "done"}, method="PATCH")
    for role in ("a", "am", "w", "w2"):
        api(path, role, BODY, status=404)
    api(path, "foreign", BODY, status=404, other_org=True)
    for body in (
        {**BODY, "text": " " * 20},
        {**BODY, "score": 5},
        {**BODY, "capability_keys": ["missing"]},
    ):
        api(path, "bm", body, status=422)
    available = org_api(f"{LIST}?demand_id={demand.id}", "bm")
    assert available[0]["default_keys"] == ["synthetic", "support"]
    subject = db.get(Employee, people["w"].id)
    assert subject is not None
    subject.employment_status = "inactive"
    db.commit()
    api(path, "admin", BODY, status=404)
    subject.employment_status = "active"
    db.commit()
    with ThreadPoolExecutor(2) as pool:
        responses = list(
            pool.map(
                lambda role: api(
                    path, role, {**BODY, "text": f"{FEEDBACK}{role}"}, status=None
                ),
                ("bm", "admin"),
            )
        )
    assert sorted(r.status_code for r in responses) == [201, 409]
    assert org_api(f"{LIST}?demand_id={demand.id}", "bm")[0]["has_feedback"]


@pytest.mark.parametrize("exempt", [False, True])
def test_feedback_publication_and_search(
    case: tuple, org_api: Callable[..., Any], db: Session, exempt: bool
) -> None:
    _, _, demand, people, _, org = case
    api = org_api
    if exempt:
        api(
            "/approval-policy",
            "admin",
            {"category_overrides": {"support": False}},
            method="PUT",
        )
    assignment_id = complete(case)
    request_id = api(
        f"/capacity/assignments/{assignment_id}/feedback", "bm", BODY, status=201
    )
    request_path = f"/me/requests/{request_id}"
    pending = owner_request(api, request_id)
    assert (
        pending["origin"] == "assignment_feedback"
        and pending["status"] == "pending_owner"
    )
    main, support = pending["items"]
    assert main["op"] == support["op"] == "add"
    assert main["after"]["level"] == "routine" and support["after"]["level"] is None
    evidence_id = main["evidence"][0]["evidence_id"]
    assert support["evidence"][0]["evidence_id"] == evidence_id
    evidence = api(f"/evidence/{evidence_id}", "w")
    assert (
        evidence["kind"] == "manager_review" and evidence["visibility"] == "restricted"
    )
    assert (
        evidence["source"] == "assignment_feedback"
        and evidence["source_ref"] == assignment_id
    )
    assert evidence["content"] == {"comment": FEEDBACK}
    assert any(n["request_id"] == request_id for n in api("/me/inbox", "w")["data"])
    api(request_path + "/submit", "w", {"revision": pending["revision"]}, status=422)
    work, _ = append_evidence(
        db,
        org_id=org.id,
        subject_employee_id=people["w"].id,
        kind=EvidenceKind.WORK_RECORD,
        source="csv:synthetic",
        source_ref="old",
        title="合成工作记录",
        content={"summary": "合成交付"},
        visibility=Visibility.STANDARD,
        provenance={},
        occurred_at=utc_now() - timedelta(days=7),
    )
    db.commit()
    updated = api(
        request_path + "/items",
        "w",
        {
            "items": [
                {
                    "op": "add",
                    "capability_key": item["capability"]["key"],
                    "level": "routine",
                    "statement": item["after"]["statement"],
                    "evidence_ids": [evidence_id]
                    + ([str(work.id)] if item is main else []),
                }
                for item in pending["items"]
            ]
        },
        method="PUT",
    )
    submitted = api(request_path + "/submit", "w", {"revision": updated["revision"]})
    assert submitted["status"] == "pending_review"
    versions = select(ProfileVersion).where(ProfileVersion.org_id == org.id)
    before = db.exec(versions).all()
    assert len(before) == int(exempt)
    if exempt:
        assert before[0].published_by["via"] == "policy"
        assert before[0].snapshot["claims"][0]["assessment_status"] == "tentative"
    api(f"/reviews/{request_id}", "w", status=403)
    decided = decide(api, submitted)
    published_id = UUID(decided["published_version_id"])
    version = db.get(ProfileVersion, published_id)
    assert (
        version
        and evidence_id in str(version.snapshot)
        and FEEDBACK not in str(version.snapshot)
    )
    main_search = f"/people-search?capability_key={demand.capability_key}"
    newer, _ = append_evidence(
        db,
        org_id=org.id,
        subject_employee_id=people["w2"].id,
        kind=work.kind,
        source=work.source,
        source_ref="newer",
        title=work.title,
        content=work.content,
        visibility=work.visibility,
        provenance={},
        occurred_at=utc_now() - timedelta(days=2),
    )
    db.commit()
    peer = api(
        "/me/requests",
        "w2",
        {
            "items": [
                {
                    "op": "add",
                    "capability_key": demand.capability_key,
                    "level": "routine",
                    "statement": "完成合成交付",
                    "evidence_ids": [str(newer.id)],
                }
            ]
        },
        status=201,
    )
    peer = api(
        f"/me/requests/{peer['id']}/submit", "w2", {"revision": peer["revision"]}
    )
    decide(api, peer, role="bm")
    baseline = api(main_search, "bm")["data"]
    assert [row["employee_id"] for row in baseline] == [
        str(people[code].id) for code in ("w2", "w")
    ]
    assert baseline[1]["evidence_count"] == 1 and baseline[1]["latest_evidence_at"]
    result = api("/people-search?capability_key=support", "bm")["data"]
    assert result[0]["employee_id"] == str(people["w"].id)
    assert result[0]["evidence_count"] == 0 and result[0]["latest_evidence_at"] is None
    assert (
        api("/people-search?capability_key=support&evidence_from=2000-01-01", "bm")[
            "count"
        ]
        == 0
    )

    # A second completed allocation adds only a new reference to the existing claim.
    followup = api(
        f"/capacity/assignments/{complete(case)}/feedback",
        "admin",
        {**BODY, "capability_keys": ["synthetic"]},
        status=201,
    )
    attached = owner_request(api, followup)
    assert (
        attached["items"][0]["op"] == "attach_evidence"
        and attached["items"][0]["after"] is None
    )
    submitted = api(
        f"/me/requests/{followup}/submit", "w", {"revision": attached["revision"]}
    )
    if exempt:
        decide(api, submitted)
        fields = ("employee_id", "evidence_count", "latest_evidence_at")
        assert [
            [row[field] for field in fields] for row in api(main_search, "bm")["data"]
        ] == [[row[field] for field in fields] for row in baseline]
        return
    decision = decide(api, submitted, "changes_requested")
    returned = owner_request(api, decision["returned_request_id"])
    assert returned["status"] == "draft"
    api(
        f"/me/requests/{returned['id']}/submit", "w", {"revision": returned["revision"]}
    )
    assert (
        api(f"/me/requests/{returned['id']}/withdraw", "w", {})["status"] == "withdrawn"
    )
    assert api(main_search, "bm")["data"] == baseline


@pytest.mark.parametrize("dual_manager", [False, True])
def test_borrowed_feedback_scope(
    case: tuple, org_api: Callable[..., Any], db: Session, dual_manager: bool
) -> None:
    api = org_api
    _, _, demand, people, users, org = case
    assignment_id = complete(case)
    row = db.get(CapacityAssignment, UUID(assignment_id))
    lender = db.get(Department, people["w"].department_id)
    assert row and row.start_date and lender
    lender.parent_department_id = None
    project = CapacityProject(
        org_id=org.id,
        title="合成项目",
        description="",
        requester_user_id=users["a"].id,
        requesting_department_id=demand.requesting_department_id,
    )
    proposal = CapacityProposal(
        org_id=org.id,
        demand_id=demand.id,
        source_option_id=uuid4(),
        demand_revision=1,
        proposed_by_user_id=users["bm"].id,
        hold_expires_at=utc_now(),
    )
    db.add_all([project, proposal])
    db.flush()
    line = CapacityProposalLine(
        org_id=org.id,
        proposal_id=proposal.id,
        employee_id=row.employee_id,
        lender_department_id=lender.id,
        start_date=row.start_date,
        end_date=row.due_date,
        allocation_percent=row.allocation_percent,
    )
    db.add(line)
    db.flush()
    row.proposal_line_id, demand.project_id, demand.role_name = (
        line.id,
        project.id,
        "合成角色",
    )
    db.commit()
    try:
        assert api(LIST, "cm")[0]["employee_name"] == people["w"].display_name
        path = f"/capacity/assignments/{assignment_id}/feedback"
        for role in ("bm", "a", "am"):
            assert api(LIST, role) == []
            api(path, role, BODY, status=404)
        membership = db.exec(
            select(OrgMembership).where(
                OrgMembership.org_id == org.id,
                OrgMembership.user_id == users["am"].id,
            )
        ).one()
        grant = RoleGrant(
            org_id=org.id,
            membership_id=membership.id,
            role="dept_manager",
            department_id=lender.id,
        )
        if dual_manager:
            db.add(grant)
            db.commit()
            assert api(LIST, "am")[0]["employee_name"] == people["w"].display_name
        request_id = api(path, "am" if dual_manager else "cm", BODY, status=201)
        evidence_id = owner_request(api, request_id)["items"][0]["evidence"][0][
            "evidence_id"
        ]
        for role in ("a", "bm") if dual_manager else ("a", "am", "bm"):
            api(f"/evidence/{evidence_id}", role, status=404)
        if not dual_manager:
            db.add(grant)
        db.commit()
        assert not api(f"/employees/{people['w'].id}/evidence", "am")["data"][0][
            "redacted"
        ]
        assert FEEDBACK in str(api(f"/evidence/{evidence_id}", "am"))
        api(path, "am", BODY, status=409)
        assert (
            api(f"/capacity/demands/{demand.id}/assignments", "bm")["data"][0][
                "headcount"
            ]
            == 1
        )
        for path in (
            f"/capacity/demands/{demand.id}",
            f"/capacity/demands/{demand.id}/history",
            f"/capacity/projects/{project.id}",
        ):
            detail = str(api(path, "a"))
            assert all(
                value not in detail
                for value in (
                    FEEDBACK,
                    request_id,
                    evidence_id,
                    people["w"].display_name,
                )
            )
    finally:
        row.proposal_line_id, demand.project_id, demand.role_name = None, None, None
        db.flush()
        db.delete(line)
        db.flush()
        db.delete(proposal)
        db.delete(project)
        db.commit()


def test_requester_managing_parent_department_can_write_feedback(
    case: tuple, org_api: Callable[..., Any], db: Session
) -> None:
    _, _, demand, people, users, _ = case
    assignment_id = complete(case)
    row = db.get(CapacityAssignment, UUID(assignment_id))
    assert row
    demand.requester_user_id = users["bm"].id
    demand.requesting_department_id = people["bm"].department_id
    demand.target_department_id = people["w"].department_id
    path = f"/capacity/assignments/{assignment_id}/feedback"
    row.employee_id = people["bm"].id
    db.commit()
    org_api(path, "bm", BODY, status=404)
    row.employee_id = people["w"].id
    db.commit()
    org_api(path, "am", BODY, status=404)
    assert org_api(f"{LIST}?demand_id={demand.id}", "bm")[0]["id"] == assignment_id
    request_id = org_api(path, "bm", BODY, status=201)
    evidence_id = owner_request(org_api, request_id)["items"][0]["evidence"][0][
        "evidence_id"
    ]
    assert FEEDBACK in str(org_api(f"/evidence/{evidence_id}", "bm"))
    org_api(f"/evidence/{evidence_id}", "am", status=404)
    org_api(f"/evidence/{evidence_id}", "foreign", status=404)


def test_feedback_migration(migration_engine: Engine) -> None:
    parent = "c836001bc001"
    migrate(migration_engine, "downgrade", parent)
    with Session(migration_engine) as session:
        org = Organization(name="合成迁移企业", slug="synthetic-feedback")
        session.add(org)
        session.flush()
        employee = Employee(org_id=org.id, display_name="合成员工")
        session.add(employee)
        session.flush()
        legacy = ProfileChangeRequest(
            org_id=org.id,
            subject_employee_id=employee.id,
            origin="import",
            status="draft",
            author={"actor_type": "user"},
        )
        session.add(legacy)
        session.flush()
        legacy_id = legacy.id
        session.commit()
    migrate(migration_engine, "upgrade", "head")
    migrate(migration_engine, "downgrade", parent)
    migrate(migration_engine, "upgrade", "head")
    with Session(migration_engine) as session:
        row = session.get(ProfileChangeRequest, legacy_id)
        assert row and row.origin == RequestOrigin.IMPORT
        row.origin = RequestOrigin.ASSIGNMENT_FEEDBACK
        session.commit()
    with pytest.raises(RuntimeError, match="存在工作反馈请求"):
        migrate(migration_engine, "downgrade", parent)
    with Session(migration_engine) as session:
        row = session.get(ProfileChangeRequest, legacy_id)
        assert row and row.origin == RequestOrigin.ASSIGNMENT_FEEDBACK
