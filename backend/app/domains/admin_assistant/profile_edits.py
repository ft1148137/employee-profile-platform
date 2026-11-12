from uuid import UUID

from pydantic import BaseModel
from sqlmodel import Session

from app.authz.principal import Principal
from app.domains.org.service import visible_employee
from app.domains.review.direct import (
    DirectEditInput,
    DirectEditResult,
    direct_edit,
    preview_direct_edit,
)


class EditArgs(DirectEditInput):
    employee_id: UUID


def impact(db: Session, p: Principal, args: EditArgs) -> BaseModel:
    from app.domains.admin_assistant.schemas import Impact

    result = preview_direct_edit(p, db, args.employee_id, args)
    subject = visible_employee(db, p, args.employee_id)
    changes = "；".join(
        f"{i.capability_name} 等级 {i.before.level if i.before and i.before.level else '未知'} → {i.after.level or '未知'}，{i.after.statement}"
        for i in result.items
    )
    return Impact(
        count=1, text=f"{subject.display_name}：{changes}，直接发布并通知本人"
    )


def apply(
    db: Session, p: Principal, args: EditArgs, *, commit: bool = True
) -> DirectEditResult:
    return direct_edit(
        p, db, args.employee_id, args, via="web", client_name="管理助手", commit=commit
    )
