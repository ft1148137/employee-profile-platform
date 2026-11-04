import json
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlmodel import Session, select

from app.domains.change_request.import_json import (
    FILES_DIR,
    ProfileImport,
    parse_import,
)
from app.domains.change_request.models import ProfileChangeRequest
from app.domains.evidence.models import Evidence
from app.domains.org.models import EmploymentStatus


def example() -> dict:
    return json.loads((FILES_DIR / "example.json").read_bytes())


def counts(db: Session, org_id: UUID) -> tuple[int, int]:
    return (
        len(
            db.exec(
                select(ProfileChangeRequest).where(
                    ProfileChangeRequest.org_id == org_id
                )
            ).all()
        ),
        len(db.exec(select(Evidence).where(Evidence.org_id == org_id)).all()),
    )


def upload(
    client: TestClient,
    demo: tuple,
    body: dict | bytes,
    confirm: bool = False,
    role: str = "employee",
    filename: str = "profile.json",
) -> Response:
    url, _, auth = demo
    return client.post(
        f"{url}/me/profile-import",
        headers=auth[role],
        params={"confirm": confirm},
        files={
            "file": (
                filename,
                body if isinstance(body, bytes) else json.dumps(body).encode(),
                "application/json",
            )
        },
    )


def test_schema_and_authenticated_downloads(
    shared_demo: tuple, client: TestClient
) -> None:
    assert (
        json.loads((FILES_DIR / "v1.schema.json").read_bytes())
        == ProfileImport.model_json_schema()
    )
    assert parse_import((FILES_DIR / "example.json").read_bytes()).items
    _, _, auth = shared_demo
    for name, filename in (
        ("schema", "v1.schema.json"),
        ("example", "example.json"),
        ("skill", "SKILL.md"),
    ):
        path = f"/api/v1/profile-import/files/{name}"
        assert client.get(path).status_code == 401
        response = client.get(path, headers=auth["employee"])
        assert response.status_code == 200
        assert response.content == (FILES_DIR / filename).read_bytes()
    assert (
        client.get(
            "/api/v1/profile-import/files/unknown", headers=auth["employee"]
        ).status_code
        == 422
    )


def test_preview_confirm_edit_and_withdraw(
    demo: tuple, client: TestClient, db: Session
) -> None:
    url, employees, auth = demo
    org_id = employees["E02"].org_id
    before = counts(db, org_id)
    profile = client.get(f"{url}/me/profile", headers=auth["employee"]).json()
    body = example()
    body["employee_email"] = body["employee_email"].upper()
    # A historical version still resolves by capability key.
    body["capability_schema_version"] = 999
    existing = next(i for i in profile["items"] if i["claim"])
    body["items"].append(
        {
            **body["items"][0],
            "capability_key": existing["capability"]["key"],
            "level": None,
        }
    )
    response = upload(client, demo, body)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert [i["after"]["op"] for i in preview["items"]] == ["add", "modify"]
    assert preview["items"][1]["before"]["id"] == existing["claim"]["id"]
    assert counts(db, org_id) == before
    assert db.get(ProfileChangeRequest, UUID(preview["request_id"])) is None
    response = upload(
        client, demo, body, True, filename="folder/" + "x" * 205 + ".json"
    )
    assert response.status_code == 200, response.text
    request_id = response.json()["request_id"]
    request = next(
        r
        for r in client.get(f"{url}/me/requests", headers=auth["employee"]).json()[
            "data"
        ]
        if r["id"] == request_id
    )
    assert request["status"] == "pending_review" and request["origin"] == "import"
    assert request["submitted_via"] == "import" and request["client_name"] == "x" * 200
    assert request["items"][0]["uncertainty"] == body["items"][0]["uncertainty"]
    reports = db.exec(
        select(Evidence).where(
            Evidence.org_id == org_id, Evidence.source == "self_report:import"
        )
    ).all()
    assert len(reports) == 2
    assert all(
        r.visibility == "restricted"
        and r.content["statement"] == body["items"][0]["basis"]
        for r in reports
    )
    assert all(r.provenance["request_id"] == request_id for r in reports)
    for role in ("admin", "employee", "manager"):
        content = client.get(
            f"{url}/evidence/{reports[0].id}", headers=auth[role]
        ).json()["content"]
        assert (content is None) == (role == "admin")
    assert client.get(f"{url}/me/profile", headers=auth["employee"]).json() == profile
    assert (
        client.post(
            f"{url}/me/requests/{request_id}/withdraw", headers=auth["employee"]
        ).json()["status"]
        == "withdrawn"
    )
    # Existing initial candidates retain uncertainty through the shared draft service.
    initial = next(
        r
        for r in client.get(f"{url}/me/requests", headers=auth["employee"]).json()[
            "data"
        ]
        if r["origin"] == "initial_sync"
    )
    item = initial["items"][0]
    updated = client.put(
        f"{url}/me/requests/{initial['id']}/items",
        headers=auth["employee"],
        json={
            "items": [
                {
                    "op": item["op"],
                    "capability_key": item["capability"]["key"],
                    "statement": "合成修改",
                    "uncertainty": item["uncertainty"],
                }
            ]
        },
    ).json()
    assert updated["items"][0]["uncertainty"] == item["uncertainty"]


@pytest.mark.parametrize(
    "body,message,status",
    [
        (b'{\n"bad":', "第 2 行", 422),
        (b"[]", "只能包含本人的一份画像", 422),
        (b"\xff", "UTF-8", 422),
        (b" " * (1024 * 1024 + 1), "1 MB", 413),
        (
            b'{"schema_version": 2, "employee_email": "invalid", "extra": true, "items": [{"capability_key": 1, "statement": []}]}',
            "第 1 项 basis",
            422,
        ),
    ],
)
def test_bad_files_are_atomic(
    demo: tuple, client: TestClient, db: Session, body: bytes, message: str, status: int
) -> None:
    _, employees, _ = demo
    before = counts(db, employees["E02"].org_id)
    response = upload(client, demo, body, True)
    assert response.status_code == status
    assert message in response.text
    if b'"extra"' in body:
        messages = response.json()["detail"]["blockers"]
        assert any("多余字段" in m for m in messages)
        assert any("字段类型" in m for m in messages)
        assert any("缺少必填字段" in m for m in messages)
    assert counts(db, employees["E02"].org_id) == before


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("employee_email", "manager.platform@demo.example.com", "不是你的登录邮箱"),
        ("employee_email", "other@foreign.example.com", "不是你的登录邮箱"),
        ("capability_key", "missing", "第 1 项 capability_key"),
        ("level", "missing", "第 1 项 level"),
        ("statement", "x" * 4001, "文字超过长度上限"),
        ("schema_version", "2", "只支持格式版本 1"),
        ("capability_schema_version", "1", "字段类型或取值不正确"),
    ],
)
def test_business_and_format_errors(
    demo: tuple,
    client: TestClient,
    db: Session,
    field: str,
    value: object,
    message: str,
) -> None:
    _, employees, _ = demo
    body = example()
    (body if field in body else body["items"][0])[field] = value
    before = counts(db, employees["E02"].org_id)
    response = upload(client, demo, body, True)
    assert response.status_code == 422, response.text
    assert message in response.text
    assert counts(db, employees["E02"].org_id) == before


def test_duplicate_and_inactive_and_foreign(
    demo: tuple, client: TestClient, db: Session
) -> None:
    _, employees, _ = demo
    body = example()
    body["items"] *= 2
    before = counts(db, employees["E02"].org_id)
    assert "第 2 项 capability_key" in upload(client, demo, body, True).text
    assert upload(client, demo, example(), True, role="foreign").status_code == 404
    assert upload(client, demo, example(), True, role="admin").status_code == 404
    employees["E02"].employment_status = EmploymentStatus.INACTIVE
    db.add(employees["E02"])
    db.commit()
    assert "仅在职员工" in upload(client, demo, example(), True).text
    assert counts(db, employees["E02"].org_id) == before
