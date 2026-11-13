from datetime import datetime
from math import ceil
from zoneinfo import ZoneInfo

from app.domains.base import utc_now

DEADLINE_MESSAGE = (
    "这次处理超过 3 分钟没完成，已经停止，没有保存任何改动。"
    "材料较多时请分批，例如名单每次 10–20 人、词典每次 20 项左右。"
    "已经上传的附件不用重新上传，直接说『用刚才的名单先建前 10 人』。"
)


def retry_time(retry_at: datetime) -> str:
    now = utc_now()
    local = retry_at.astimezone(ZoneInfo("Asia/Shanghai"))
    day = (
        ""
        if local.date() == now.astimezone(local.tzinfo).date()
        else f"{local.month} 月 {local.day} 日 "
    )
    minutes = max(1, ceil((retry_at - now).total_seconds() / 60))
    return f"北京时间 {day}{local:%H:%M}（约 {minutes} 分钟后）"
