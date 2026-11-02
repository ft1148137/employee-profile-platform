import subprocess
import sys
from uuid import uuid4

from sqlalchemy import Engine, inspect
from sqlmodel import Session, select

from app.domains.org.models import Organization
from app.domains.setup.models import SetupStatus, SetupStepState
from app.models import User


def test_setup_migration_roundtrip(migration_engine: Engine) -> None:
    with Session(migration_engine) as session:
        org = Organization(slug=str(uuid4()), name="合成迁移测试")
        user = User(username=str(uuid4()), hashed_password="unused")
        session.add_all([org, user])
        session.flush()
        session.add(
            SetupStepState(
                org_id=org.id,
                step_key="roster",
                status=SetupStatus.COMPLETED,
                updated_by_user_id=user.id,
            )
        )
        session.commit()
        org_id = org.id
    for action, revision in (("downgrade", "b62c194f83d5"), ("upgrade", "head")):
        subprocess.run(
            [sys.executable, "-m", "alembic", action, revision],
            check=True,
            capture_output=True,
        )
        assert ("setup_step_state" in inspect(migration_engine).get_table_names()) == (
            action == "upgrade"
        )
        assert inspect(migration_engine).has_table("login_attempt")
    with Session(migration_engine) as session:
        assert session.get(Organization, org_id).name == "合成迁移测试"
        assert session.exec(select(SetupStepState)).all() == []
