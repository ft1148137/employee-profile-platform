import csv
import io
from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.domains.capability.models import CapabilityDefinition
from app.domains.evidence.models import Evidence
from app.domains.org.models import Organization
from app.domains.profile.models import ProfileVersion


def test_csv_localization_and_unchanged_json(
    demo: tuple, client: TestClient, db: Session
) -> None:
    url, employees, auth = demo
    employee = employees["E01"]
    org = db.get_one(Organization, employee.org_id)
    org.timezone = "America/New_York"
    employee.employee_code = "=001"
    dictionary = client.get(f"{url}/capabilities", headers=auth["admin"]).json()
    capability = db.get_one(CapabilityDefinition, dictionary["data"][0]["id"])
    capability.name = '+合成,"能力"'
    capability.level_rubric = {"levels": [{"key": "routine", "label": "熟练"}]}
    for version in db.exec(
        select(ProfileVersion).where(ProfileVersion.org_id == org.id)
    ).all():
        version.snapshot = {"claims": []}
        db.add(version)
    instant = datetime(2026, 7, 1, 3, 4, 59, tzinfo=UTC)
    evidence = Evidence(
        org_id=org.id,
        subject_employee_id=employee.id,
        kind="self_report",
        source="synthetic",
        source_ref=str(uuid4()),
        title="Synthetic evidence",
        content={},
        provenance={},
        content_hash=str(uuid4()),
        visibility="restricted",
        occurred_at=instant,
    )
    claims = [
        {
            "id": str(uuid4()),
            "capability_definition_id": str(capability.id),
            "statement": "Synthetic statement",
            "level": None if status == "tentative" else "routine",
            "assessment_status": status,
            "confidence": None,
            "explanation": None,
            "generated_by": {"actor_type": "system_worker"},
            "evidence": []
            if status == "tentative"
            else [
                {
                    "id": str(evidence.id),
                    "kind": "self_report",
                    "title": evidence.title,
                    "relation": "supports",
                }
            ],
        }
        for status in ("confirmed", "tentative", "disputed")
    ]
    db.add_all(
        [
            org,
            employee,
            capability,
            evidence,
            ProfileVersion(
                org_id=org.id,
                subject_employee_id=employee.id,
                revision=100,
                schema_version_id=dictionary["schema_version"]["id"],
                snapshot={"claims": claims},
                published_at=instant,
                published_by={"actor_type": "system_worker"},
            ),
        ]
    )
    db.commit()

    response = client.get(f"{url}/profile-export", headers=auth["manager"])
    assert response.status_code == 200
    assert response.content.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert rows[0] == [
        "工号",
        "能力",
        "等级",
        "判断状态",
        "证据数",
        "最近证据时间",
        "批准人",
        "批准时间",
    ]
    assert sorted(rows[1:]) == sorted(
        [
            "'=001",
            "'" + capability.name,
            "未定级" if status == "暂定" else "熟练",
            status,
            "0" if status == "暂定" else "1",
            "" if status == "暂定" else "2026-06-30 23:04",
            "system_worker",
            "2026-06-30 23:04",
        ]
        for status in ("已确认", "暂定", "存在争议")
    )
    json_response = client.get(
        f"{url}/profile-export?format=json", headers=auth["manager"]
    )
    assert json_response.status_code == 200
    assert sorted(json_response.json(), key=lambda row: row["status"]) == sorted(
        [
            {
                "employee_code": "=001",
                "capability_key": capability.key,
                "capability_name": capability.name,
                "level": None if status == "tentative" else "routine",
                "level_name": None if status == "tentative" else "熟练",
                "status": status,
                "evidence_count": 0 if status == "tentative" else 1,
                "latest_evidence_at": None
                if status == "tentative"
                else "2026-07-01T03:04:59Z",
                "published_by": "system_worker",
                "published_at": "2026-07-01T03:04:59Z",
            }
            for status in ("confirmed", "tentative", "disputed")
        ],
        key=lambda row: row["status"],
    )
