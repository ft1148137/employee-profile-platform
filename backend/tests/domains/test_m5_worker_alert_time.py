from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import Engine
from sqlmodel import Session, select

from app.domains.inbox.models import InboxItem
from app.domains.org.models import Organization, OrgMembership, Role, RoleGrant
from app.models import User
from app.worker.models import WorkerHeartbeat
from app.worker.monitor import dispatch_worker_alerts


def test_alert_times_follow_each_tenant_and_remain_deduplicated(
    migration_engine: Engine,
) -> None:
    beat_at = datetime(2026, 11, 14, 21, 31, 20, 123456, tzinfo=UTC)
    now = beat_at + timedelta(minutes=6)
    with Session(migration_engine) as db:
        beijing = Organization(slug=f"alert-bj-{uuid4()}", name="合成北京企业")
        tokyo = Organization(
            slug=f"alert-tokyo-{uuid4()}",
            name="合成东京企业",
            timezone="Asia/Tokyo",
        )
        for org in (beijing, tokyo):
            user = User(
                username=f"alert-{uuid4()}@example.com", hashed_password="synthetic"
            )
            db.add(org)
            db.add(user)
            db.flush()
            member = OrgMembership(org_id=org.id, user_id=user.id)
            db.add(member)
            db.flush()
            db.add(
                RoleGrant(org_id=org.id, membership_id=member.id, role=Role.ORG_ADMIN)
            )
        db.add(WorkerHeartbeat(beat_at=beat_at))
        db.commit()
        expected = {
            beijing.id: "2026-11-15 05:31",
            tokyo.id: "2026-11-15 06:31",
        }
        dispatch_worker_alerts(db, now)
        first_messages = {}
        for org_id, local_time in expected.items():
            item = db.exec(select(InboxItem).where(InboxItem.org_id == org_id)).one()
            assert f"最后心跳：{local_time}。" in item.message
            assert "+00:00" not in item.message
            assert "T" not in item.message
            assert "123456" not in item.message
            first_messages[org_id] = item.message

    with Session(migration_engine) as restarted:
        dispatch_worker_alerts(restarted, now + timedelta(seconds=10))
        for org_id, first_message in first_messages.items():
            items = restarted.exec(
                select(InboxItem).where(InboxItem.org_id == org_id)
            ).all()
            assert len(items) == 1
            assert items[0].message == first_message
