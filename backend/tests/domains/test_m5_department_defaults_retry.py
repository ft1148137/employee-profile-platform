from collections.abc import Generator
from datetime import timedelta
from unittest.mock import Mock

import pytest
from sqlalchemy import Engine, inspect
from sqlmodel import Session, select

from app.authz.principal import Principal
from app.core.secrets import SecretUnavailable
from app.domains.base import utc_now
from app.domains.capability import department_defaults as d
from app.domains.capability.service import activate_template, read_editor, save_version
from app.domains.model.client import (
    ModelOutputInvalid,
    ModelRateLimited,
    ModelUnavailable,
)
from app.domains.org.models import Department, Organization
from tests.utils.migrations import migrate
from tests.utils.org import delete_test_organization


@pytest.fixture
def retry_defaults(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> Generator[tuple[Principal, Department, Mock, Mock]]:
    org = Organization(slug="m5-retry-synthetic", name="重试测试企业")
    db.add(org)
    db.commit()
    p = Principal.system_worker(org.id)
    activate_template(db, p, "hospital")
    department = Department(org_id=p.org_id, name="测试部门")
    db.add(department)
    db.commit()
    config = Mock(return_value=object())
    generate = Mock(return_value=[])
    monkeypatch.setattr(d, "model_config", config)
    monkeypatch.setattr(d, "generate", generate)
    yield p, department, config, generate
    db.rollback()
    for row in db.exec(select(Department).where(Department.org_id == p.org_id)).all():
        row.capability_schema_version_id = None
        db.add(row)
    db.commit()
    delete_test_organization(db, p.org_id)


@pytest.mark.parametrize(
    "failure",
    [
        ModelUnavailable(),
        ModelRateLimited(None),
        ModelOutputInvalid(),
        SecretUnavailable(),
    ],
)
def test_failure_cools_down_then_retries_and_clears(
    retry_defaults: tuple, db: Session, failure: Exception
) -> None:
    p, department, config, generate = retry_defaults
    failed_call = config if isinstance(failure, SecretUnavailable) else generate
    failed_call.side_effect = failure
    d.fill_department_defaults(p)
    failed_call.side_effect = None
    d.fill_department_defaults(p)
    assert failed_call.call_count == 1
    db.refresh(department)
    assert department.capability_failed_at is not None
    assert department.capability_failed_at >= utc_now() - timedelta(minutes=1)
    assert department.capability_source is None

    department.capability_failed_at = utc_now() - timedelta(hours=25)
    db.add(department)
    db.commit()
    d.fill_department_defaults(p)
    db.refresh(department)
    assert failed_call.call_count == 2
    assert department.capability_source == "pi"
    assert department.capability_failed_at is None


def test_cooldown_is_per_department_and_success_can_be_recalculated(
    retry_defaults: tuple, db: Session
) -> None:
    p, department, config, generate = retry_defaults
    generate.side_effect = ModelUnavailable()
    d.fill_department_defaults(p)
    db.refresh(department)
    failed_at = department.capability_failed_at
    other = Department(org_id=p.org_id, name="新部门")
    db.add(other)
    db.commit()
    generate.side_effect = None
    d.fill_department_defaults(p)
    db.refresh(other)
    db.refresh(department)
    assert config.call_count == generate.call_count == 2
    assert other.capability_source == "pi"
    assert other.capability_failed_at is None
    assert department.capability_failed_at == failed_at

    save_version(db, p, read_editor(db, p).definitions)
    db.commit()
    d.fill_department_defaults(p)
    assert config.call_count == generate.call_count == 3


def test_cooldown_expires_at_24_hours(
    retry_defaults: tuple, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    p, department, config, generate = retry_defaults
    now = utc_now()
    monkeypatch.setattr(d, "utc_now", lambda: now)
    department.capability_failed_at = now - timedelta(hours=24) + timedelta(seconds=1)
    db.add(department)
    db.commit()
    d.fill_department_defaults(p)
    assert not config.called and not generate.called
    department.capability_failed_at = now - timedelta(hours=24)
    db.add(department)
    db.commit()
    d.fill_department_defaults(p)
    db.refresh(department)
    assert generate.call_count == 1
    assert department.capability_failed_at is None


def test_retry_migration_roundtrip(migration_engine: Engine) -> None:
    migrate(migration_engine, "downgrade", "b489001bc001")
    assert "capability_failed_at" not in {
        c["name"] for c in inspect(migration_engine).get_columns("department")
    }
    migrate(migration_engine, "upgrade", "head")
    column = next(
        c
        for c in inspect(migration_engine).get_columns("department")
        if c["name"] == "capability_failed_at"
    )
    assert column["nullable"] and column["type"].timezone
