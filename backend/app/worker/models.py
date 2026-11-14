from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime
from sqlmodel import Field, SQLModel


class WorkerHeartbeat(SQLModel, table=True):
    """Singleton process state, with no tenant data or org_id."""

    __tablename__ = "worker_heartbeat"
    __table_args__ = (CheckConstraint("id = 1", name="ck_worker_heartbeat_singleton"),)
    id: int = Field(default=1, primary_key=True)
    beat_at: datetime = Field(sa_type=DateTime(timezone=True))
