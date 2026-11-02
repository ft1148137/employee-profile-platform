import hashlib
import uuid
from collections.abc import Generator
from functools import partial

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, select

from app.core.db import engine
from app.domains.audit.models import ActorKind, AuditEvent
from app.domains.audit.service import record_audit
from app.domains.base import utc_now
from app.domains.capability.models import CapabilitySchemaVersion, SchemaStatus
from app.domains.evidence.models import Evidence, EvidenceKind, Visibility
from app.domains.evidence.service import append_evidence
from app.domains.org.models import (
    Department,
    Employee,
    ExternalIdentity,
    Organization,
    OrgMembership,
    Role,
    RoleGrant,
)
from app.domains.profile.models import ProfileVersion
from app.models import User

type Rows = tuple[
    Organization,
    Organization,
    Employee,
    Department,
    OrgMembership,
    CapabilitySchemaVersion,
]


@pytest.fixture
def session() -> Generator[Session]:
    with Session(engine) as session:
        yield session
        session.rollback()


@pytest.fixture
def rows(session: Session) -> Rows:
    a, b = (
        Organization(slug=str(uuid.uuid7()), name="Synthetic org") for _ in range(2)
    )
    user = User(
        username=str(uuid.uuid7()),
        email=f"{uuid.uuid7()}@example.com",
        hashed_password="unused",
    )
    session.add_all([a, b, user])
    session.flush()
    dept = Department(org_id=a.id, name="Engineering")
    schema = CapabilitySchemaVersion(org_id=a.id, version=1, status=SchemaStatus.ACTIVE)
    session.add_all([dept, schema])
    session.flush()
    employee = Employee(
        org_id=a.id, department_id=dept.id, display_name="Synthetic employee"
    )
    session.add(employee)
    session.flush()
    membership = OrgMembership(org_id=a.id, user_id=user.id, employee_id=employee.id)
    session.add(membership)
    session.flush()
    return a, b, employee, dept, membership, schema


def test_database_constraints(session: Session, rows: Rows) -> None:
    a, b, employee, dept, membership, schema = rows
    identity = ExternalIdentity(
        org_id=a.id, employee_id=employee.id, source="jira", external_id="synthetic"
    )
    version = ProfileVersion(
        org_id=a.id,
        subject_employee_id=employee.id,
        revision=1,
        schema_version_id=schema.id,
        snapshot={},
        published_at=utc_now(),
        published_by={},
    )
    grant = RoleGrant(org_id=a.id, membership_id=membership.id, role=Role.ORG_ADMIN)
    session.add_all([identity, version, grant])
    session.flush()
    invalid: list[tuple[SQLModel, str]] = [
        (
            ExternalIdentity(
                org_id=a.id,
                employee_id=employee.id,
                source="jira",
                external_id="synthetic",
            ),
            "23505",
        ),
        (ProfileVersion(**(version.model_dump() | {"id": uuid.uuid7()})), "23505"),
        (
            RoleGrant(org_id=a.id, membership_id=membership.id, role=Role.ORG_ADMIN),
            "23505",
        ),
        (
            RoleGrant(org_id=a.id, membership_id=membership.id, role=Role.DEPT_MANAGER),
            "23514",
        ),
        (
            RoleGrant(
                org_id=a.id,
                membership_id=membership.id,
                role=Role.ORG_ADMIN,
                department_id=dept.id,
            ),
            "23514",
        ),
        (
            CapabilitySchemaVersion(org_id=a.id, version=2, status=SchemaStatus.ACTIVE),
            "23505",
        ),
        (Department(org_id=b.id, name="Other", parent_department_id=dept.id), "23503"),
        (
            OrgMembership(
                org_id=b.id, user_id=membership.user_id, employee_id=employee.id
            ),
            "23503",
        ),
    ]
    for row, sqlstate in invalid:
        with pytest.raises(IntegrityError) as error, session.begin_nested():
            session.add(row)
            session.flush()
        assert error.value.orig.sqlstate == sqlstate  # type: ignore[attr-defined]


def test_evidence_idempotency_and_tenant_boundary(session: Session, rows: Rows) -> None:
    a, b, employee, *_ = rows
    append = partial(
        append_evidence,
        session,
        org_id=a.id,
        subject_employee_id=employee.id,
        kind=EvidenceKind.SELF_REPORT,
        source="synthetic",
        source_ref="1",
        visibility=Visibility.RESTRICTED,
        provenance={},
    )
    first, created = append(
        title="Original", content={"b": {"z": 2, "a": 1}, "a": "示例"}
    )
    second, repeated = append(
        title="Ignored duplicate", content={"a": "示例", "b": {"a": 1, "z": 2}}
    )
    assert created and not repeated
    assert first.id == second.id and second.title == "Original"
    assert (
        first.content_hash
        == hashlib.sha256('{"a":"示例","b":{"a":1,"z":2}}'.encode()).hexdigest()
    )
    assert first.id.version == 7 and first.created_at.utcoffset().total_seconds() == 0
    with pytest.raises(IntegrityError) as error, session.begin_nested():
        append(org_id=b.id, title="Wrong tenant", content={})
    assert error.value.orig.sqlstate == "23503"  # type: ignore[attr-defined]
    session.rollback()
    assert session.exec(select(Evidence).where(Evidence.org_id == a.id)).first() is None


def test_audit_joins_callers_transaction(session: Session, rows: Rows) -> None:
    a, *_ = rows
    for org_id in (a.id, None):
        event = record_audit(
            session,
            org_id=org_id,
            actor_type=ActorKind.SEED,
            actor_id=None,
            action="test",
            target_type="synthetic",
            target_id=None,
            details={"test": True},
        )
        assert event.id.version == 7
        assert session.get(AuditEvent, event.id) is event
    event_id = event.id
    session.rollback()
    assert session.get(AuditEvent, event_id) is None
