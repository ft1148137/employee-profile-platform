from uuid import uuid4

import pytest
from sqlalchemy import Engine, Uuid, inspect
from sqlmodel import Session, select

from app.domains.im.models import ImNotification
from app.domains.inbox.models import InboxItem, InboxKind
from app.domains.org.models import Employee, Organization
from tests.utils.migrations import migrate


def test_im_borrow_assignment_migration(migration_engine: Engine) -> None:
    migrate(migration_engine, "downgrade", "c840001bc001")
    assert "borrow_assignment_id" not in {
        column["name"]
        for column in inspect(migration_engine).get_columns("im_notification")
    }
    migrate(migration_engine, "upgrade", "head")
    assert any(
        column["name"] == "borrow_assignment_id"
        and isinstance(column["type"], Uuid)
        and column["nullable"]
        for column in inspect(migration_engine).get_columns("im_notification")
    )


def test_im_borrow_assignment_downgrade_blocks_pending_reminders(
    migration_engine: Engine,
) -> None:
    assignment_id = uuid4()
    with Session(migration_engine) as db:
        org = Organization(slug=str(uuid4()), name="合成企业")
        db.add(org)
        db.flush()
        employee = Employee(org_id=org.id, display_name="合成员工")
        db.add(employee)
        db.flush()
        for source_id in (assignment_id, None):
            item = InboxItem(
                org_id=org.id,
                recipient_employee_id=employee.id,
                kind=InboxKind.CAPACITY,
                message="合成跟踪提醒",
            )
            db.add(item)
            db.flush()
            db.add(
                ImNotification(
                    org_id=org.id,
                    employee_id=employee.id,
                    inbox_item_id=item.id,
                    platform="telegram",
                    borrow_assignment_id=source_id,
                )
            )
        org_id = org.id
        db.commit()

    with pytest.raises(
        RuntimeError, match="存在待发借调 IM 提醒，无法降级并保留分配来源"
    ):
        migrate(migration_engine, "downgrade", "c840001bc001")

    with Session(migration_engine) as db:
        notifications = db.exec(
            select(ImNotification).where(ImNotification.org_id == org_id)
        ).all()
        assert {row.borrow_assignment_id: row.status for row in notifications} == {
            assignment_id: "pending",
            None: "pending",
        }
        for row in notifications:
            if row.borrow_assignment_id == assignment_id:
                row.status = "sent"
        db.commit()

    migrate(migration_engine, "downgrade", "c840001bc001")
    assert "borrow_assignment_id" not in {
        column["name"]
        for column in inspect(migration_engine).get_columns("im_notification")
    }
    migrate(migration_engine, "upgrade", "head")
    with Session(migration_engine) as db:
        notifications = db.exec(
            select(ImNotification).where(ImNotification.org_id == org_id)
        ).all()
        assert sorted(row.status for row in notifications) == ["pending", "sent"]
        assert all(row.borrow_assignment_id is None for row in notifications)
