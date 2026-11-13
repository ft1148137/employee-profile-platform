from uuid import uuid4

import pytest
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.domains.capability.models import CapabilityProposal
from app.domains.org.models import Employee, Organization
from tests.utils.migrations import insert_historical_rows, migrate


def test_employee_suggestion_migration_roundtrip(migration_engine: Engine) -> None:
    migrate(migration_engine, "downgrade", "b381001dc001")
    with Session(migration_engine) as session:
        org = Organization(slug=str(uuid4()), name="Synthetic suggestions")
        other = Organization(slug=str(uuid4()), name="Synthetic foreign")
        insert_historical_rows(session, org, other)
        employee = Employee(org_id=org.id, display_name="Synthetic employee")
        foreign = Employee(org_id=other.id, display_name="Synthetic foreign")
        insert_historical_rows(session, employee, foreign)
        proposal = CapabilityProposal(
            org_id=org.id,
            name="建议",
            name_key="建议",
            description="说明",
            reason="理由",
        )
        insert_historical_rows(session, proposal)
        session.commit()
    migrate(migration_engine, "upgrade", "head")
    with migration_engine.begin() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT proposed_by_employee_id FROM capability_proposal WHERE id=:id"
                ),
                {"id": proposal.id},
            ).scalar_one()
            is None
        )
        connection.execute(
            text(
                "UPDATE capability_proposal SET proposed_by_employee_id=:employee WHERE id=:id"
            ),
            {"id": proposal.id, "employee": employee.id},
        )
    with pytest.raises(IntegrityError), migration_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE capability_proposal SET proposed_by_employee_id=:employee WHERE id=:id"
            ),
            {"id": proposal.id, "employee": foreign.id},
        )
    migrate(migration_engine, "downgrade", "b381001dc001")
    assert "proposed_by_employee_id" not in {
        c["name"] for c in inspect(migration_engine).get_columns("capability_proposal")
    }
    migrate(migration_engine, "upgrade", "head")
    with migration_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT proposed_by_employee_id FROM capability_proposal WHERE id=:id"
                ),
                {"id": proposal.id},
            ).scalar_one()
            is None
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM alembic_version")
            ).scalar_one()
            == 1
        )
