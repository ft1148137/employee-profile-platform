from uuid import uuid4

import pytest
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.domains.change_request.models import (
    ProfileChangeRequest,
    RequestOrigin,
    RequestStatus,
)
from app.domains.org.models import Employee, Organization, OrgMembership
from app.models import User
from tests.utils.migrations import migrate


def test_review_user_migration_preserves_decision_identity(
    migration_engine: Engine,
) -> None:
    migrate(migration_engine, "downgrade", "b117001bc001")
    decision_id, inbox_id = uuid4(), uuid4()
    with Session(migration_engine) as session:
        org = Organization(slug=str(uuid4()), name="Synthetic migration")
        user = User(
            username=str(uuid4()),
            email=f"{uuid4()}@example.com",
            hashed_password="unused",
        )
        session.add_all([org, user])
        session.flush()
        reviewer = Employee(org_id=org.id, display_name="Synthetic reviewer")
        subject = Employee(org_id=org.id, display_name="Synthetic subject")
        session.add_all([reviewer, subject])
        session.flush()
        member = OrgMembership(org_id=org.id, user_id=user.id, employee_id=reviewer.id)
        request = ProfileChangeRequest(
            org_id=org.id,
            subject_employee_id=subject.id,
            origin=RequestOrigin.MANUAL,
            status=RequestStatus.PENDING_REVIEW,
            author={},
        )
        session.add_all([member, request])
        session.flush()
        session.execute(
            text("""
            INSERT INTO review_decision
            (id,org_id,created_at,request_id,reviewer_membership_id,acting_for_membership_id,via,request_revision)
            VALUES (:id,:org,now(),:request,:member,:member,'web',1)
        """),
            {
                "id": decision_id,
                "org": org.id,
                "request": request.id,
                "member": member.id,
            },
        )
        session.execute(
            text("""
            INSERT INTO inbox_item (id,org_id,created_at,recipient_employee_id,kind,request_id,message)
            VALUES (:id,:org,now(),:employee,'review_requested',:request,'Synthetic review')
        """),
            {
                "id": inbox_id,
                "org": org.id,
                "employee": reviewer.id,
                "request": request.id,
            },
        )
        user_id, member_id = user.id, member.id
        session.commit()
    migrate(migration_engine, "upgrade", "head")
    with migration_engine.connect() as connection:
        assert connection.execute(
            text(
                "SELECT reviewer_user_id,acting_for_user_id FROM review_decision WHERE id=:id"
            ),
            {"id": decision_id},
        ).one() == (user_id, user_id)
        assert connection.execute(
            text(
                "SELECT recipient_employee_id,recipient_user_id FROM inbox_item WHERE id=:id"
            ),
            {"id": inbox_id},
        ).one() == (None, user_id)
        assert any(
            fk["referred_columns"] == ["org_id", "user_id"]
            for fk in inspect(connection).get_foreign_keys("inbox_item")
        )
    for statement in (
        "UPDATE inbox_item SET recipient_user_id=NULL WHERE id=:id",
        "UPDATE inbox_item SET recipient_employee_id=recipient_user_id WHERE id=:id",
        "UPDATE inbox_item SET recipient_user_id=:foreign WHERE id=:id",
    ):
        with pytest.raises(IntegrityError), migration_engine.begin() as connection:
            connection.execute(text(statement), {"id": inbox_id, "foreign": uuid4()})
    migrate(migration_engine, "downgrade", "b117001bc001")
    with migration_engine.connect() as connection:
        assert connection.execute(
            text(
                "SELECT reviewer_membership_id,acting_for_membership_id FROM review_decision WHERE id=:id"
            ),
            {"id": decision_id},
        ).one() == (member_id, member_id)
        assert (
            connection.execute(
                text("SELECT count(*) FROM inbox_item WHERE id=:id"), {"id": inbox_id}
            ).scalar_one()
            == 0
        )
    migrate(migration_engine, "upgrade", "head")
    with migration_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT reviewer_user_id FROM review_decision WHERE id=:id"),
                {"id": decision_id},
            ).scalar_one()
            == user_id
        )
