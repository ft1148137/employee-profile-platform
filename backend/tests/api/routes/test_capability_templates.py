from collections.abc import Generator
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import settings
from app.core.db import engine
from app.core.security import create_access_token
from app.domains.audit.models import AuditEvent
from app.domains.capability import service
from app.domains.capability.models import (
    CapabilityDefinition,
    CapabilitySchemaVersion,
    SchemaStatus,
)
from app.domains.capability.templates import get_template, load_catalog
from app.domains.org.models import (
    Department,
    Organization,
    OrgMembership,
    Role,
    RoleGrant,
)
from app.models import User
from app.seed.loader import load_demo
from app.seed.schema import read_bundle
from tests.utils.org import delete_test_organization


def headers(user_id: UUID) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {create_access_token(user_id, expires_delta=timedelta(minutes=5))}"
    }


@pytest.fixture
def enterprise(db: Session) -> Generator[tuple[UUID, dict[str, dict[str, str]]]]:
    orgs = [
        Organization(slug=str(uuid4()), name="Synthetic enterprise") for _ in range(2)
    ]
    db.add_all(orgs)
    db.flush()
    department = Department(org_id=orgs[0].id, name="Synthetic department")
    db.add(department)
    db.flush()
    auth = {}
    for name in ("admin", "manager", "employee", "foreign"):
        org = orgs[name == "foreign"]
        user = User(username=str(uuid4()), hashed_password="unused")
        db.add(user)
        db.flush()
        membership = OrgMembership(org_id=org.id, user_id=user.id)
        db.add(membership)
        db.flush()
        if name != "employee":
            db.add(
                RoleGrant(
                    org_id=org.id,
                    membership_id=membership.id,
                    role=Role.DEPT_MANAGER if name == "manager" else Role.ORG_ADMIN,
                    department_id=department.id if name == "manager" else None,
                )
            )
        auth[name] = headers(user.id)
    db.commit()
    yield orgs[0].id, auth
    db.rollback()
    for org in orgs:
        delete_test_organization(db, org.id)


def test_catalog_and_preview(enterprise: tuple, client: TestClient) -> None:
    _, auth = enterprise
    for path in ("/capability-templates", "/capability-templates/hospital"):
        assert client.get(f"/api/v1{path}").status_code == 401
    response = client.get("/api/v1/capability-templates", headers=auth["employee"])
    assert response.status_code == 200
    catalog = response.json()
    assert len(catalog) == 11
    assert catalog[0]["id"] == "tech-rnd" and catalog[0]["recommended"] is True
    for row, template in zip(
        catalog, load_catalog(settings.CAPABILITY_TEMPLATES_DIR), strict=True
    ):
        assert row == template.public().model_dump()
    response = client.get(
        "/api/v1/capability-templates/hospital", headers=auth["manager"]
    )
    assert response.status_code == 200
    preview = response.json()
    template = get_template("hospital")
    assert preview["capability_count"] == len(template.capabilities)
    assert len(preview["capabilities"]) == len(template.capabilities)
    for row, capability in zip(
        preview["capabilities"], template.capabilities, strict=True
    ):
        assert row == {
            **capability.model_dump(exclude={"level_rubric"}),
            "levels": capability.level_rubric.model_dump()["levels"],
        }
    response = client.get("/api/v1/capability-templates/missing", headers=auth["admin"])
    assert response.status_code == 404
    assert response.json()["detail"] == "没有这个词典模板"


def test_activation_and_idempotency(
    enterprise: tuple, client: TestClient, db: Session
) -> None:
    org_id, auth = enterprise
    url = f"/api/v1/orgs/{org_id}"
    response = client.post(
        f"{url}/capability-schema",
        json={"template_id": "tech-rnd"},
        headers=auth["admin"],
    )
    assert response.status_code == 200
    first = response.json()
    assert first["version"] == 1 and first["activated_at"]
    assert first["template_ref"] == get_template("tech-rnd").template_ref
    for template_id, version in (("tech-rnd", 1), ("hospital", 2), ("hospital", 2)):
        response = client.post(
            f"{url}/capability-schema",
            json={"template_id": template_id},
            headers=auth["admin"],
        )
        assert response.status_code == 200
        assert response.json()["version"] == version
        if version == 1:
            assert response.json() == first
        template = get_template(template_id)
        read = client.get(f"{url}/capabilities", headers=auth["employee"])
        assert read.status_code == 200
        assert read.json()["schema_version"] == response.json()
        assert [
            {k: v for k, v in row.items() if k != "id"} for row in read.json()["data"]
        ] == [capability.model_dump() for capability in template.capabilities]
    schemas = db.exec(
        select(CapabilitySchemaVersion)
        .where(CapabilitySchemaVersion.org_id == org_id)
        .order_by(CapabilitySchemaVersion.version)
    ).all()
    assert [(s.version, s.status) for s in schemas] == [
        (1, SchemaStatus.RETIRED),
        (2, SchemaStatus.ACTIVE),
    ]
    events = db.exec(
        select(AuditEvent)
        .where(AuditEvent.org_id == org_id)
        .order_by(AuditEvent.created_at)
    ).all()
    assert len(events) == 2
    for event, schema, template_id in zip(
        events, schemas, ("tech-rnd", "hospital"), strict=True
    ):
        assert event.action == "capability_schema.activated"
        assert event.target_id == str(schema.id)
        assert event.actor_type == "user" and event.actor_id
        assert event.details == {
            "template_id": template_id,
            "template_ref": get_template(template_id).template_ref,
            "schema_version_id": str(schema.id),
            "capability_count": len(get_template(template_id).capabilities),
        }


@pytest.mark.parametrize(
    ("role", "status"), [("manager", 403), ("employee", 403), ("foreign", 404)]
)
def test_activation_permissions(
    enterprise: tuple, client: TestClient, db: Session, role: str, status: int
) -> None:
    org_id, auth = enterprise
    response = client.post(
        f"/api/v1/orgs/{org_id}/capability-schema",
        json={"template_id": "hospital"},
        headers=auth[role],
    )
    assert response.status_code == status
    assert not db.exec(
        select(CapabilitySchemaVersion).where(CapabilitySchemaVersion.org_id == org_id)
    ).all()


def test_missing_template(enterprise: tuple, client: TestClient) -> None:
    org_id, auth = enterprise
    response = client.post(
        f"/api/v1/orgs/{org_id}/capability-schema",
        json={"template_id": "missing"},
        headers=auth["admin"],
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "没有这个词典模板"


def test_claims_survive_activation(
    db: Session, seed_path: Path, client: TestClient
) -> None:
    with Session(engine) as session:
        load_demo(session, seed_path)
    org = db.exec(
        select(Organization).where(Organization.slug == read_bundle(seed_path).org.slug)
    ).one()
    admin = db.exec(select(User).where(User.email == "admin@demo.example.com")).one()
    from app.domains.profile.models import ProfileClaim, ProfileVersion

    before = {
        model: [
            r.model_dump(mode="json")
            for r in db.exec(
                select(model).where(model.org_id == org.id).order_by(model.id)
            ).all()
        ]
        for model in (ProfileClaim, ProfileVersion)
    }
    for template_id in ("tech-rnd", "hospital"):
        response = client.post(
            f"/api/v1/orgs/{org.id}/capability-schema",
            json={"template_id": template_id},
            headers=headers(admin.id),
        )
        assert response.status_code == 200
        db.expire_all()
        for model, original in before.items():
            assert [
                r.model_dump(mode="json")
                for r in db.exec(
                    select(model).where(model.org_id == org.id).order_by(model.id)
                ).all()
            ] == original
    assert db.exec(
        select(AuditEvent).where(
            AuditEvent.org_id == org.id,
            AuditEvent.action == "capability_schema.activated",
        )
    ).all()


def test_activation_rolls_back_on_audit_failure(
    enterprise: tuple, client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    org_id, auth = enterprise
    url = f"/api/v1/orgs/{org_id}/capability-schema"
    assert (
        client.post(
            url, json={"template_id": "tech-rnd"}, headers=auth["admin"]
        ).status_code
        == 200
    )

    def fail_audit(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("Synthetic audit failure")

    monkeypatch.setattr(service, "record_audit", fail_audit)
    with pytest.raises(RuntimeError, match="Synthetic audit failure"):
        client.post(url, json={"template_id": "hospital"}, headers=auth["admin"])
    schemas = db.exec(
        select(CapabilitySchemaVersion).where(CapabilitySchemaVersion.org_id == org_id)
    ).all()
    assert len(schemas) == 1 and schemas[0].status == SchemaStatus.ACTIVE
    definitions = db.exec(
        select(CapabilityDefinition).where(CapabilityDefinition.org_id == org_id)
    ).all()
    assert len(definitions) == len(get_template("tech-rnd").capabilities)
