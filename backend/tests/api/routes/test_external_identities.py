from datetime import UTC, datetime
from typing import Any
from uuid import uuid7

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session, select

from app.core.db import engine
from app.domains.audit.models import AuditEvent
from app.domains.evidence.models import Evidence, EvidenceStatus
from app.domains.org.models import EmploymentStatus, ExternalIdentity
from app.domains.work.ingest import SourcePerson, SourceRecord, ingest_record
from app.domains.work.models import WorkEvent
from tests.authz.test_tenant_access import headers, tenant_data  # noqa: F401


def account(
    db: Session, org: Any, external: str = "synthetic-account", **kwargs: Any
) -> ExternalIdentity:
    identity = ExternalIdentity(
        org_id=org.id, source="jira", external_id=external, **kwargs
    )
    db.add(identity)
    db.commit()
    return identity


def links(*pairs: tuple[Any, Any]) -> dict[str, Any]:
    return {
        "links": [
            {"identity_id": str(i.id), "employee_id": str(e.id)} for i, e in pairs
        ]
    }


def ingest(
    db: Session, org: Any, *accounts: ExternalIdentity, key: str = "SYN-1"
) -> None:
    ingest_record(
        db,
        org_id=org.id,
        record=SourceRecord(
            source="jira",
            key=key,
            title="Synthetic task",
            occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
            people=[
                SourcePerson(
                    external_id=i.external_id, role="assignee", event_ref=str(n)
                )
                for n, i in enumerate(accounts)
            ],
        ),
        provenance={},
    )
    db.commit()


def test_candidates_and_batch_queries(
    request: pytest.FixtureRequest, client: TestClient, db: Session
) -> None:
    orgs, _, employees, users = request.getfixturevalue("tenant_data")
    employees[0].work_email = " Exact@Example.com "
    employees[1].display_name = employees[2].display_name = "王静"
    employees[3].employment_status = EmploymentStatus.INACTIVE
    employees[3].work_email = "inactive@example.com"
    db.add_all(employees)
    db.commit()
    email = account(
        db, orgs[0], "email", email="exact@example.COM", display_name="王静"
    )
    name = account(db, orgs[0], "name", display_name=employees[4].display_name)
    duplicate = account(db, orgs[0], "duplicate", display_name="王静")
    inactive = account(db, orgs[0], "inactive", display_name=employees[3].display_name)
    left = account(db, orgs[0], "left", email=" INACTIVE@example.com ")
    foreign = account(db, orgs[1], "foreign", display_name=employees[4].display_name)
    ingest(db, orgs[0], email, name)
    ingest(db, orgs[0], email, key="SYN-2")
    # Ingestion refreshes account metadata; restore the synthetic candidate inputs.
    email.email, email.display_name = "exact@example.COM", "王静"
    name.display_name = employees[4].display_name
    db.add_all([email, name])
    db.commit()
    url, auth = f"/api/v1/orgs/{orgs[0].id}/external-identities", headers(users[0])
    result = client.get(url, headers=auth).json()
    data = {row["id"]: row for row in result["data"]}
    assert result["count"] == 5 and str(foreign.id) not in data
    assert data[str(email.id)]["candidate"]["reason"] == "email"
    assert data[str(email.id)]["candidate"]["id"] == str(employees[0].id)
    assert data[str(email.id)]["record_count"] == 2
    assert data[str(email.id)]["linked_at"] is None
    assert data[str(name.id)]["candidate"]["reason"] == "name"
    assert data[str(name.id)]["record_count"] == 1
    assert data[str(duplicate.id)]["candidate"] is None
    assert data[str(inactive.id)]["candidate"] is None
    assert data[str(left.id)]["candidate"]["reason"] == "email"
    assert data[str(left.id)]["candidate"]["employment_status"] == "inactive"
    for q in ("email", "exact@example", "王静"):
        assert client.get(url, headers=auth, params={"q": q}).json()["count"] > 0
    assert client.get(url, headers=auth, params={"q": "%"}).json()["count"] == 0
    assert (
        client.get(url, headers=auth, params={"source": "csv:PLM"}).json()["count"] == 0
    )
    counts = []
    statements: list[Any] = []

    def capture(*args: Any) -> None:
        statements.append(args)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        for size in (1, 200):
            statements.clear()
            assert (
                client.get(url, headers=auth, params={"limit": size}).status_code == 200
            )
            counts.append(len(statements))
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert counts[0] == counts[1]
    # Duplicate normalized emails are ambiguous; same-name active employees still work.
    employees[4].work_email = employees[0].work_email
    db.add(employees[4])
    db.commit()
    assert (
        next(
            r
            for r in client.get(url, headers=auth).json()["data"]
            if r["id"] == str(email.id)
        )["candidate"]
        is None
    )
    name.source = "csv:PLM"
    db.add(name)
    db.commit()
    row = client.get(url, headers=auth, params={"source": "csv:PLM"}).json()["data"][0]
    assert row["source_label"] == "PLM" and row["candidate"]["reason"] == "name"
    assert client.get(url, headers=auth, params={"skip": 200}).json()["data"] == []


def test_link_unlink_restore(
    request: pytest.FixtureRequest, client: TestClient, db: Session
) -> None:
    orgs, _, employees, users = request.getfixturevalue("tenant_data")
    org, employee = orgs[0], employees[0]
    identity = account(db, org)
    ingest(db, org, identity)
    url, auth = f"/api/v1/orgs/{org.id}/external-identities", headers(users[0])
    body = links((identity, employee))
    result = client.post(url + "/link", headers=auth, json=body)
    assert result.status_code == 200 and result.json()["linked"] == 1
    assert result.json()["counts"]["evidence_added"] == 1
    db.refresh(identity)
    assert identity.linked_by == f"user:{users[0].id}" and identity.linked_at
    assert client.post(url + "/link", headers=auth, json=body).json()["linked"] == 0
    assert client.get(url, headers=auth).json()["count"] == 0
    row = client.get(url, headers=auth, params={"status": "linked"}).json()["data"][0]
    assert row["employee"]["id"] == str(employee.id) and row["candidate"] is None
    assert row["employee"]["department"]["name"] is not None
    assert datetime.fromisoformat(row["linked_at"]) == identity.linked_at
    original = db.exec(select(Evidence).where(Evidence.org_id == org.id)).one()
    original_content = original.content.copy()
    # Same record ingested after linking yields exactly the same active content.
    ingest(db, org, identity)
    assert (
        db.exec(select(Evidence).where(Evidence.org_id == org.id)).one().content
        == original_content
    )
    assert (
        client.post(url + f"/{identity.id}/unlink", headers=auth).json()[
            "evidence_retracted"
        ]
        == 1
    )
    db.refresh(original)
    db.refresh(identity)
    assert (
        original.status == EvidenceStatus.RETRACTED
        and original.status_reason == "管理员解除了账号对应"
    )
    assert (
        original.content == original_content
        and identity.employee_id is identity.linked_by is identity.linked_at is None
    )
    evidence_url = f"/api/v1/orgs/{org.id}/employees/{employee.id}/evidence"
    assert client.get(evidence_url, headers=auth).json()["count"] == 0
    assert (
        client.get(
            evidence_url, headers=auth, params={"include_inactive": True}
        ).json()["count"]
        == 1
    )
    assert client.post(url + f"/{identity.id}/unlink", headers=auth).status_code == 409
    assert (
        client.post(url + "/link", headers=auth, json=body).json()["counts"][
            "evidence_added"
        ]
        == 1
    )
    active = db.exec(
        select(Evidence).where(
            Evidence.org_id == org.id, Evidence.status == EvidenceStatus.ACTIVE
        )
    ).one()
    assert active.content["restored_from"] == str(original.id)
    assert client.post(url + f"/{identity.id}/unlink", headers=auth).status_code == 200
    assert (
        client.post(
            url + "/link", headers=auth, json=links((identity, employees[1]))
        ).status_code
        == 200
    )
    db.refresh(active)
    db.refresh(original)
    assert active.status == original.status == EvidenceStatus.RETRACTED
    new = db.exec(
        select(Evidence).where(
            Evidence.org_id == org.id, Evidence.status == EvidenceStatus.ACTIVE
        )
    ).one()
    assert new.subject_employee_id == employees[1].id
    assert len(db.exec(select(WorkEvent).where(WorkEvent.org_id == org.id)).all()) == 1
    audits = db.exec(select(AuditEvent).where(AuditEvent.org_id == org.id)).all()
    assert [a.action for a in audits].count("external_identity.linked") == 3
    assert [a.action for a in audits].count("evidence.retracted") == 2
    for audit in audits:
        assert identity.external_id not in str(audit.details)
        if audit.action.startswith("external_identity."):
            assert set(audit.details) == {"identity_id", "employee_id", "source"}


def test_multi_account_and_atomicity(
    request: pytest.FixtureRequest, client: TestClient, db: Session
) -> None:
    orgs, _, employees, users = request.getfixturevalue("tenant_data")
    org, employee = orgs[0], employees[0]
    first, second, third = [account(db, org, str(i)) for i in range(3)]
    ingest(db, org, first, second)
    ingest(db, org, first, key="SYN-2")
    url, auth = f"/api/v1/orgs/{org.id}/external-identities", headers(users[0])
    assert (
        client.post(
            url + "/link",
            headers=auth,
            json=links((first, employee), (second, employee)),
        ).json()["linked"]
        == 2
    )
    for body, status in (
        (links((third, employee), (first, employees[1])), 409),
        (links((third, employees[-1])), 422),
        (links((third, employee), (third, employees[1])), 409),
        (
            {"links": [{"identity_id": str(uuid7()), "employee_id": str(employee.id)}]},
            404,
        ),
        ({"links": []}, 422),
        ({"links": links((third, employee))["links"] * 201}, 422),
    ):
        assert client.post(url + "/link", headers=auth, json=body).status_code == status
        db.refresh(third)
        assert third.employee_id is None
    assert (
        client.post(url + f"/{first.id}/unlink", headers=auth).json()[
            "evidence_retracted"
        ]
        == 1
    )
    active = db.exec(
        select(Evidence).where(
            Evidence.org_id == org.id, Evidence.status == EvidenceStatus.ACTIVE
        )
    ).one()
    assert active.source_ref == "SYN-1"
    assert (
        client.post(url + f"/{second.id}/unlink", headers=auth).json()[
            "evidence_retracted"
        ]
        == 1
    )
    assert (
        client.post(
            url + "/link",
            headers=auth,
            json=links((third, employee), (third, employee)),
        ).json()["linked"]
        == 1
    )


def test_permissions(
    request: pytest.FixtureRequest, client: TestClient, db: Session
) -> None:
    orgs, _, employees, users = request.getfixturevalue("tenant_data")
    identity, foreign = [account(db, org) for org in orgs]
    url = f"/api/v1/orgs/{orgs[0].id}/external-identities"
    for index, status in ((1, 403), (2, 403), (3, 404), (4, 404)):
        for method, path, body in (
            ("GET", url, None),
            ("POST", url + "/link", links((identity, employees[0]))),
            ("POST", url + f"/{identity.id}/unlink", None),
        ):
            assert (
                client.request(
                    method, path, headers=headers(users[index]), json=body
                ).status_code
                == status
            )
    auth = headers(users[0])
    assert (
        client.post(
            url + "/link", headers=auth, json=links((foreign, employees[0]))
        ).status_code
        == 404
    )
    for identity_id in (foreign.id, uuid7()):
        assert (
            client.post(url + f"/{identity_id}/unlink", headers=auth).status_code == 404
        )
    for params in ({"limit": 201}, {"skip": -1}, {"status": "invalid"}):
        assert client.get(url, headers=auth, params=params).status_code == 422


def test_link_order_equivalence(
    request: pytest.FixtureRequest, client: TestClient, db: Session
) -> None:
    orgs, _, employees, users = request.getfixturevalue("tenant_data")
    org, employee = orgs[0], employees[0]
    url, auth = f"/api/v1/orgs/{org.id}/external-identities", headers(users[0])
    contents = []
    for before in (False, True):
        identity = account(db, org, f"order-{before}")
        if not before:
            ingest(db, org, identity, key=f"SYN-{before}")
        assert (
            client.post(
                url + "/link", headers=auth, json=links((identity, employee))
            ).status_code
            == 200
        )
        if before:
            db.refresh(identity)
            ingest(db, org, identity, key=f"SYN-{before}")
        evidence = db.exec(
            select(Evidence).where(
                Evidence.org_id == org.id, Evidence.source_ref == f"SYN-{before}"
            )
        ).one()
        contents.append({k: v for k, v in evidence.content.items() if k != "key"})
        assert evidence.event_id and evidence.status == EvidenceStatus.ACTIVE
    assert contents[0] == contents[1]


def test_my_identities_are_linked_and_scoped_to_principal(
    request: pytest.FixtureRequest, client: TestClient, db: Session
) -> None:
    orgs, _, employees, users = request.getfixturevalue("tenant_data")
    mine = account(db, orgs[0], "mine", employee_id=employees[2].id)
    account(db, orgs[0], "manager", employee_id=employees[1].id)
    account(db, orgs[0], "unlinked")
    account(db, orgs[1], "foreign", employee_id=employees[5].id)
    url = f"/api/v1/orgs/{orgs[0].id}/me/external-identities"
    auth = headers(users[2])
    response = client.get(
        url,
        headers=auth,
        params={"employee_id": str(employees[1].id), "status": "all"},
    )
    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert [row["id"] for row in response.json()["data"]] == [str(mine.id)]
    assert response.json()["data"][0]["candidate"] is None
    assert client.get(url, headers=auth, params={"skip": 1}).json()["data"] == []
    manager = client.get(url, headers=headers(users[1])).json()
    assert manager["count"] == 1 and manager["data"][0]["external_id"] == "manager"
    assert client.get(url, headers=headers(users[0])).status_code == 404
    assert client.get(url, headers=headers(users[3])).status_code == 404
    assert (
        client.get(
            f"/api/v1/orgs/{orgs[1].id}/me/external-identities", headers=auth
        ).status_code
        == 404
    )
    for params in ({"limit": 201}, {"limit": 0}, {"skip": -1}):
        assert client.get(url, headers=auth, params=params).status_code == 422
