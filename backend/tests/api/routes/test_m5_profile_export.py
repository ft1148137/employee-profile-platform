import csv
import io
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.domains.audit.models import AuditEvent
from app.domains.base import utc_now
from app.domains.evidence.models import Evidence
from app.domains.org.models import Department
from app.domains.profile.models import ProfileVersion
from app.models import User
from tests.api.routes.test_mcp import token_for

COLUMNS = [
    "employee_code",
    "capability_key",
    "capability_name",
    "level",
    "level_name",
    "status",
    "evidence_count",
    "latest_evidence_at",
    "published_by",
    "published_at",
]
CSV_COLUMNS = [
    "工号",
    "能力",
    "等级",
    "判断状态",
    "证据数",
    "最近证据时间",
    "批准人",
    "批准时间",
]


@pytest.fixture
def export_data(demo: tuple, db: Session, client: TestClient) -> tuple:
    url, employees, auth = demo
    org_id = employees["E01"].org_id
    dictionary = client.get(f"{url}/capabilities", headers=auth["admin"]).json()
    capabilities = dictionary["data"][:2]
    manager = db.exec(
        select(User).where(User.email == "manager.platform@demo.example.com")
    ).one()
    child = Department(
        org_id=org_id,
        name="合成下级",
        parent_department_id=employees["E01"].department_id,
    )
    upper = Department(org_id=org_id, name="合成上级")
    db.add_all([child, upper])
    db.flush()
    department = db.get_one(Department, employees["E01"].department_id)
    department.parent_department_id = upper.id
    employees["E03"].department_id = child.id
    employees["E04"].department_id = upper.id
    employees["E05"].employment_status = "inactive"
    db.add_all([department, *employees.values()])
    for version in db.exec(
        select(ProfileVersion).where(ProfileVersion.org_id == org_id)
    ).all():
        version.snapshot = {"claims": []}
        db.add(version)
    now = utc_now().replace(microsecond=0)
    for code in ("E01", "E02", "E03", "E04", "E05", "E06"):
        employee = employees[code]
        refs = []
        for status, occurred in (
            ("active", now - timedelta(days=30)),
            ("retracted", now),
        ):
            evidence = Evidence(
                org_id=org_id,
                subject_employee_id=employee.id,
                kind="self_report",
                source="synthetic",
                source_ref=str(uuid4()),
                title="SECRET_TITLE",
                content={"text": "SECRET_RESTRICTED_BODY"},
                provenance={},
                content_hash=str(uuid4()),
                visibility="restricted",
                status=status,
                occurred_at=occurred,
            )
            db.add(evidence)
            refs.append(
                {
                    "id": str(evidence.id),
                    "kind": "self_report",
                    "title": evidence.title,
                    "relation": "supports",
                }
            )
        claims = [
            {
                "id": str(uuid4()),
                "capability_definition_id": capability["id"],
                "statement": "SECRET_STATEMENT",
                "explanation": "SECRET_EXPLANATION",
                "level": None if code == "E02" else "routine",
                "assessment_status": "tentative" if code == "E02" else "confirmed",
                "confidence": None,
                "generated_by": {"actor_type": "user"},
                "evidence": refs,
            }
            for capability in capabilities
        ]
        for revision in (20, 21):
            db.add(
                ProfileVersion(
                    org_id=org_id,
                    subject_employee_id=employee.id,
                    revision=revision,
                    schema_version_id=dictionary["schema_version"]["id"],
                    snapshot={"claims": claims},
                    published_at=now - timedelta(days=21 - revision),
                    published_by={"actor_type": "user", "user_id": str(manager.id)}
                    if revision == 20
                    else {"actor_type": "system_worker"},
                )
            )
    db.commit()
    return url, employees, auth, capabilities, now


@pytest.mark.parametrize(
    "role,expected",
    [
        ("manager", {"E01", "E02", "E03"}),
        ("admin", {"E01", "E02", "E03", "E04", "E06"}),
    ],
)
def test_export_scope_fields_and_audit(
    export_data: tuple, client: TestClient, db: Session, role: str, expected: set[str]
) -> None:
    url, employees, auth, capabilities, now = export_data
    responses = [
        client.get(
            f"{url}/profile-export", params={"format": format}, headers=auth[role]
        )
        for format in ("json", "csv")
    ]
    assert all(response.status_code == 200 for response in responses)
    json_response, csv_response = responses
    rows = json_response.json()
    assert len(rows) == len(expected) * len(capabilities)
    assert {r["employee_code"] for r in rows} == expected
    assert {r["capability_key"] for r in rows} == {c["key"] for c in capabilities}
    for row in rows:
        assert list(row) == COLUMNS
        assert row["published_by"] == employees["E01"].display_name
        assert datetime.fromisoformat(row["published_at"]) == now - timedelta(days=1)
        assert datetime.fromisoformat(row["latest_evidence_at"]) == now - timedelta(
            days=30
        )
        assert datetime.fromisoformat(row["latest_evidence_at"]).tzinfo is not None
        assert row["evidence_count"] == 1
        assert row["status"] == (
            "tentative" if row["employee_code"] == "E02" else "confirmed"
        )
        assert row["level"] == (None if row["employee_code"] == "E02" else "routine")
    for response in responses:
        assert "SECRET" not in response.text
        assert response.headers["cache-control"] == "no-store"
        assert "attachment" in response.headers["content-disposition"]
    assert csv_response.content.startswith(b"\xef\xbb\xbf")
    csv_rows = list(
        csv.DictReader(io.StringIO(csv_response.content.decode("utf-8-sig")))
    )
    assert csv_rows == [
        dict(
            zip(
                CSV_COLUMNS,
                [
                    row["employee_code"] or "",
                    row["capability_name"],
                    row["level_name"] or "未定级",
                    "暂定" if row["status"] == "tentative" else "已确认",
                    str(row["evidence_count"]),
                    (now - timedelta(days=30))
                    .astimezone(ZoneInfo("Asia/Shanghai"))
                    .strftime("%Y-%m-%d %H:%M"),
                    row["published_by"],
                    (now - timedelta(days=1))
                    .astimezone(ZoneInfo("Asia/Shanghai"))
                    .strftime("%Y-%m-%d %H:%M"),
                ],
                strict=True,
            )
        )
        for row in rows
    ]
    assert all(c["name"] in csv_response.text for c in capabilities)
    doc = (Path(__file__).resolve().parents[4] / "docs/profile-export.md").read_text()
    assert all(f"| `{column}` |" in doc for column in COLUMNS)
    events = db.exec(
        select(AuditEvent)
        .where(
            AuditEvent.org_id == employees["E01"].org_id,
            AuditEvent.action == "profile.exported",
        )
        .order_by(AuditEvent.created_at)
    ).all()
    assert len(events) == 2
    for event, format in zip(events, ("json", "csv"), strict=True):
        assert event.details == {
            "format": format,
            "row_count": len(rows),
            "filters": {},
        }
        assert event.actor_id is not None
        assert event.target_id == str(employees["E01"].org_id)


@pytest.mark.parametrize("format", ["json", "csv"])
def test_export_denies_other_roles_and_tenants(
    export_data: tuple, client: TestClient, db: Session, format: str
) -> None:
    url, employees, auth, *_ = export_data
    pat_headers, _ = token_for(db, employees["E01"])
    for headers, status in (
        (auth["employee"], 403),
        (auth["foreign"], 404),
        ({}, 401),
        (pat_headers, 401),
    ):
        assert (
            client.get(
                f"{url}/profile-export", params={"format": format}, headers=headers
            ).status_code
            == status
        )
    assert not db.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == employees["E01"].org_id,
            AuditEvent.action == "profile.exported",
        )
    ).all()


def test_export_pages_all_judgments(
    export_data: tuple, client: TestClient, db: Session
) -> None:
    url, employees, auth, *_ = export_data
    version = db.exec(
        select(ProfileVersion).where(
            ProfileVersion.org_id == employees["E01"].org_id,
            ProfileVersion.subject_employee_id == employees["E01"].id,
            ProfileVersion.revision == 21,
        )
    ).one()
    claim = version.snapshot["claims"][0]
    version.snapshot = {"claims": [{**claim, "id": str(uuid4())} for _ in range(105)]}
    db.add(version)
    db.commit()
    response = client.get(f"{url}/profile-export?format=json", headers=auth["manager"])
    assert response.status_code == 200
    assert len(response.json()) == 109


def test_export_empty_and_invalid_format(
    export_data: tuple, client: TestClient, db: Session
) -> None:
    url, employees, auth, *_ = export_data
    assert (
        client.get(
            f"{url}/profile-export?format=xml", headers=auth["manager"]
        ).status_code
        == 422
    )
    for version in db.exec(
        select(ProfileVersion).where(ProfileVersion.org_id == employees["E01"].org_id)
    ).all():
        version.snapshot = {"claims": []}
        db.add(version)
    db.commit()
    assert (
        client.get(f"{url}/profile-export?format=json", headers=auth["manager"]).json()
        == []
    )
    response = client.get(f"{url}/profile-export", headers=auth["manager"])
    assert list(csv.reader(io.StringIO(response.content.decode("utf-8-sig")))) == [
        CSV_COLUMNS
    ]


@pytest.mark.parametrize(
    "code", ["=1+1", "+1", "-1", "@SUM(1)", "\t=1", "\r=1", "\n=1", '合成,"工号"']
)
def test_csv_cells_are_safe_and_json_preserves_codes(
    export_data: tuple, client: TestClient, db: Session, code: str
) -> None:
    url, employees, auth, *_ = export_data
    employees["E01"].employee_code = code
    db.add(employees["E01"])
    db.commit()
    json_rows = client.get(
        f"{url}/profile-export?format=json", headers=auth["manager"]
    ).json()
    assert sum(r["employee_code"] == code for r in json_rows) == 2
    response = client.get(f"{url}/profile-export?format=csv", headers=auth["manager"])
    rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
    safe = code if code.startswith("合成") else "'" + code
    assert sum(r["工号"] == safe for r in rows) == 2
