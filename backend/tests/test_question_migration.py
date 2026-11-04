from sqlalchemy import Engine, inspect, text
from sqlmodel import Session

from app.domains.inbox.models import InboxItem, InboxKind, PendingQuestion
from app.domains.org.models import Employee, Organization
from tests.utils.migrations import migrate

PREVIOUS = "a96e001bc001"


def test_question_roundtrip(migration_engine: Engine) -> None:
    with Session(migration_engine) as session:
        org = Organization(slug="question-migration", name="Synthetic migration tenant")
        session.add(org)
        session.flush()
        employee = Employee(org_id=org.id, display_name="Synthetic employee")
        session.add(employee)
        session.flush()
        question = PendingQuestion(
            org_id=org.id,
            target_employee_id=employee.id,
            question="合成问题",
            reason="合成原因",
        )
        session.add(question)
        session.flush()
        session.add(
            InboxItem(
                org_id=org.id,
                recipient_employee_id=employee.id,
                question_id=question.id,
                kind=InboxKind.QUESTION,
                message=question.question,
            )
        )
        session.commit()
    migrate(migration_engine, "downgrade", PREVIOUS)
    with migration_engine.connect() as connection:
        assert "pending_question" not in inspect(connection).get_table_names()
        assert (
            connection.execute(
                text("SELECT count(*) FROM inbox_item WHERE question_id IS NOT NULL")
            ).scalar_one()
            == 0
        )
    migrate(migration_engine, "upgrade", "head")
    with migration_engine.connect() as connection:
        inspector = inspect(connection)
        assert "pending_question" in inspector.get_table_names()
        assert (
            len(
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).all()
            )
            == 1
        )
        assert any(
            fk["constrained_columns"] == ["org_id", "question_id"]
            and fk["referred_table"] == "pending_question"
            for fk in inspector.get_foreign_keys("inbox_item")
        )
        assert {
            tuple(fk["constrained_columns"])
            for fk in inspector.get_foreign_keys("pending_question")
        } >= {
            ("org_id", "target_employee_id"),
            ("org_id", "capability_definition_id"),
            ("org_id", "answer_evidence_id"),
        }
    migrate(migration_engine, "downgrade", PREVIOUS)
    migrate(migration_engine, "upgrade", "head")
