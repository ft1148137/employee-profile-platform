import sys
from datetime import datetime, timedelta

from sqlalchemy import Engine
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

from app.domains.base import utc_now
from app.worker.models import WorkerHeartbeat

HEARTBEAT_TIMEOUT = timedelta(minutes=5)
WORKER_ALERT_PREFIX = "后台任务心跳异常，"
WORKER_ALERT_NOTIFICATION = "后台任务已停止更新，请登录网页查看「后台任务」状态卡。"
WORKER_FIX_STEPS = [
    "请联系部署维护人员，查看后台服务状态：docker compose ps。",
    "检查数据库连接和后台日志：docker compose logs --tail=100 worker。",
    "排除故障后重启后台任务：docker compose restart worker。",
]


def write_heartbeat(engine: Engine) -> None:
    now = utc_now()
    with engine.begin() as connection:
        connection.execute(
            insert(WorkerHeartbeat)
            .values(id=1, beat_at=now)
            .on_conflict_do_update(index_elements=["id"], set_={"beat_at": now})
        )


def is_fresh(beat_at: datetime | None, now: datetime) -> bool:
    return beat_at is not None and now - beat_at <= HEARTBEAT_TIMEOUT


def heartbeat_summary(beat_at: datetime | None, now: datetime) -> str:
    if beat_at is None:
        return "尚未收到后台任务心跳，请检查服务是否启动"
    seconds = max(0, int((now - beat_at).total_seconds()))
    return f"距上次心跳 {seconds // 60} 分 {seconds % 60} 秒"


def main() -> int:
    from app.core.db import engine

    try:
        with Session(engine) as db:
            row = db.get(WorkerHeartbeat, 1)
            return 0 if is_fresh(row.beat_at if row else None, utc_now()) else 1
    except SQLAlchemyError:
        sys.stderr.write("后台任务心跳检查失败：无法读取运行状态。\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
