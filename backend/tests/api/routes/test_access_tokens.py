import hashlib
import json
from datetime import datetime, timedelta
from uuid import UUID

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, inspect
from sqlmodel import Session, select

from app.core.config import settings as app_settings
from app.core.security import ALGORITHM
from app.domains.access_token.models import PersonalAccessToken
from app.domains.audit.models import AuditEvent
from app.domains.org.models import Organization, OrgMembership
from tests.utils.migrations import migrate


def test_lifecycle_secrets_and_closed_switch(
    demo: tuple, client: TestClient, db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    url, people, auth = demo
    endpoint, settings = f"{url}/me/access-tokens", f"{url}/mcp-setting"
    assert client.get(settings, headers=auth["employee"]).json() == {
        "enabled": False,
        "allowed_clients": [],
        "updated_by": None,
        "updated_at": None,
    }
    assert (
        client.post(
            endpoint, headers=auth["employee"], json={"name": "电脑"}
        ).status_code
        == 403
    )
    body = {"enabled": True, "allowed_clients": ["Cursor", "Cursor"]}
    changed = client.put(settings, headers=auth["admin"], json=body)
    assert changed.status_code == 200
    assert changed.json()["allowed_clients"] == ["Cursor"]
    assert changed.json()["updated_by"]
    assert changed.json()["updated_at"]
    response = client.post(
        endpoint, headers=auth["employee"], json={"name": " 工作电脑 "}
    )
    assert response.status_code == 201, response.text
    assert response.headers["Cache-Control"] == "no-store"
    created = response.json()
    assert created["name"] == "工作电脑"
    assert created["client_kind"] == "pat" and created["scopes"] == "employee"
    assert created["last_used_at"] is None and created["revoked_at"] is None
    lifetime = datetime.fromisoformat(
        created["expires_at"].replace("Z", "+00:00")
    ) - datetime.fromisoformat(created["created_at"].replace("Z", "+00:00"))
    assert abs(lifetime - timedelta(days=90)) < timedelta(seconds=2)
    row = db.get(PersonalAccessToken, UUID(created["id"]))
    assert row.org_id == people["E02"].org_id and row.employee_id == people["E02"].id
    assert row.token_hash == hashlib.sha256(created["token"].encode()).hexdigest()
    assert created["token"] not in row.model_dump_json()
    assert created["prefix"] == created["token"][:12]
    listing = client.get(endpoint, headers=auth["employee"])
    assert listing.headers["Cache-Control"] == "no-store"
    assert listing.json() == [{k: v for k, v in created.items() if k != "token"}]
    assert client.get(endpoint, headers=auth["manager"]).json() == []
    assert (
        client.delete(f"{endpoint}/{row.id}", headers=auth["manager"]).status_code
        == 404
    )
    assert (
        client.put(settings, headers=auth["admin"], json={"enabled": False}).status_code
        == 200
    )
    assert (
        client.post(
            endpoint, headers=auth["employee"], json={"name": "新电脑"}
        ).status_code
        == 403
    )
    assert client.get(endpoint, headers=auth["employee"]).json()[0]["id"] == str(row.id)
    revoked = client.delete(f"{endpoint}/{row.id}", headers=auth["employee"])
    assert revoked.status_code == 200 and revoked.json()["revoked_at"]
    assert (
        client.delete(f"{endpoint}/{row.id}", headers=auth["employee"]).json()
        == revoked.json()
    )
    audit = db.exec(select(AuditEvent).where(AuditEvent.org_id == row.org_id)).all()
    token_events = [a for a in audit if a.action.startswith("access_token.")]
    assert {a.action for a in token_events} == {
        "access_token.created",
        "access_token.revoked",
    }
    assert len(token_events) == 2
    assert all(a.details == {"prefix": row.prefix} for a in token_events)
    assert any(a.action == "mcp_setting.updated" for a in audit)
    public = (
        listing.text
        + revoked.text
        + json.dumps([a.model_dump(mode="json") for a in audit])
        + caplog.text
    )
    assert created["token"] not in public and row.token_hash not in public


def test_tenant_and_employee_boundaries(
    demo: tuple, client: TestClient, db: Session
) -> None:
    url, _, auth = demo
    endpoint, settings = f"{url}/me/access-tokens", f"{url}/mcp-setting"
    assert (
        client.put(
            settings, headers=auth["employee"], json={"enabled": True}
        ).status_code
        == 403
    )
    assert (
        client.put(
            settings, headers=auth["manager"], json={"enabled": True}
        ).status_code
        == 403
    )
    assert (
        client.put(settings, headers=auth["admin"], json={"enabled": True}).status_code
        == 200
    )
    assert (
        client.post(
            endpoint, headers=auth["admin"], json={"name": "无员工身份"}
        ).status_code
        == 404
    )
    row = client.post(endpoint, headers=auth["employee"], json={"name": "测试"}).json()
    foreign_user_id = UUID(
        jwt.decode(
            auth["foreign"]["Authorization"].split()[1],
            app_settings.SECRET_KEY,
            algorithms=[ALGORITHM],
        )["sub"]
    )
    foreign = db.exec(
        select(Organization)
        .join(OrgMembership)
        .where(OrgMembership.user_id == foreign_user_id)
    ).first()
    # A second membership proves even the same user cannot use records across tenants.
    token = db.get(PersonalAccessToken, UUID(row["id"]))
    db.add(OrgMembership(org_id=foreign.id, user_id=token.user_id, employee_id=None))
    db.commit()
    foreign_url = f"/api/v1/orgs/{foreign.id}/me/access-tokens"
    assert client.get(foreign_url, headers=auth["employee"]).json() == []
    assert (
        client.delete(
            f"{foreign_url}/{row['id']}", headers=auth["employee"]
        ).status_code
        == 404
    )
    assert client.get(endpoint, headers=auth["foreign"]).status_code == 404
    assert (
        client.delete(f"{endpoint}/{row['id']}", headers=auth["foreign"]).status_code
        == 404
    )
    assert client.get(settings, headers=auth["foreign"]).status_code == 404
    assert (
        client.put(
            settings, headers=auth["foreign"], json={"enabled": True}
        ).status_code
        == 404
    )
    for body in (
        {"name": ""},
        {"name": " "},
        {"name": "a" * 101},
        {"name": "test", "employee_id": str(token.employee_id)},
        {"name": "test", "org_id": str(foreign.id)},
    ):
        assert (
            client.post(endpoint, headers=auth["employee"], json=body).status_code
            == 422
        )
    assert (
        client.put(
            settings, headers=auth["admin"], json={"allowed_clients": [""]}
        ).status_code
        == 422
    )
    db.refresh(token)
    assert token.revoked_at is None


def test_migration_roundtrip(migration_engine: Engine) -> None:
    migrate(migration_engine, "downgrade", "b102001bc001")
    assert not {"personal_access_token", "mcp_setting"} & set(
        inspect(migration_engine).get_table_names()
    )
    migrate(migration_engine, "upgrade", "head")
    inspector = inspect(migration_engine)
    assert {"personal_access_token", "mcp_setting"} <= set(inspector.get_table_names())
    foreign_keys = inspector.get_foreign_keys("personal_access_token")
    assert any(
        fk["constrained_columns"] == ["org_id", "employee_id"] for fk in foreign_keys
    )
    assert any(
        fk["constrained_columns"] == ["org_id", "user_id"] for fk in foreign_keys
    )
    assert "token" not in {
        c["name"] for c in inspector.get_columns("personal_access_token")
    }
