from datetime import datetime
from math import ceil
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlmodel import Session

from app.authz.principal import Principal
from app.domains.base import utc_now
from app.domains.bot import schemas as s
from app.domains.capacity import demand
from app.domains.capacity.settings import read_settings as read_capacity_settings

DEADLINE_MESSAGE = (
    "这次处理超过 3 分钟没完成，已经停止，没有保存任何改动。"
    "材料较多时请分批，例如名单每次 10–20 人、词典每次 20 项左右。"
    "已经上传的附件不用重新上传，直接说『用刚才的名单先建前 10 人』。"
)


def capacity_failure(db: Session, principal: Principal, message: str) -> s.TextMessage:
    if not read_capacity_settings(principal, db).enabled:
        return s.TextMessage(text=message)
    links: list[s.CardLink] = []
    try:
        demand.require_requester(principal, db)
    except HTTPException:
        pass
    else:
        links.append(s.CardLink(label="我提的需求", href="/capacity?tab=outgoing"))
    if principal.is_org_admin or principal.managed_department_ids:
        links.append(s.CardLink(label="收到的需求", href="/capacity?tab=incoming"))
    lines = [
        message,
        *(
            f"可以在「需求与容量 → {link.label}」里手工"
            f"{'提交需求' if link.label == '我提的需求' else '生成方案和答复'}：{link.href}"
            for link in links
        ),
    ]
    return s.TextMessage(text="\n".join(lines), capacity_links=links)


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
