from uuid import UUID

from sqlmodel import Session, col, func, select

from app.authz.principal import Principal
from app.domains.base import utc_now
from app.domains.change_request.models import ProfileChangeRequest
from app.domains.inbox.models import InboxItem, InboxKind
from app.domains.inbox.schemas import InboxList, InboxPublic


def notify(
    session: Session, request: ProfileChangeRequest, kind: InboxKind, message: str
) -> None:
    session.add(
        InboxItem(
            org_id=request.org_id,
            recipient_employee_id=request.subject_employee_id,
            request_id=request.id,
            kind=kind,
            message=message,
        )
    )


def my_inbox(
    principal: Principal, session: Session, skip: int = 0, limit: int = 50
) -> InboxList:
    from app.domains.change_request.employee import employee_id

    query = select(InboxItem).where(
        InboxItem.org_id == principal.org_id,
        InboxItem.recipient_employee_id == employee_id(principal),
    )
    count = session.exec(select(func.count()).select_from(query.subquery())).one()
    rows = session.exec(
        query.order_by(col(InboxItem.created_at).desc(), col(InboxItem.id))
        .offset(skip)
        .limit(limit)
    ).all()
    return InboxList(
        data=[InboxPublic.model_validate(row) for row in rows], count=count
    )


def mark_read(principal: Principal, session: Session, inbox_id: UUID) -> InboxPublic:
    from app.domains.change_request.employee import EmployeeRecordNotFound, employee_id

    item = session.exec(
        select(InboxItem)
        .where(
            InboxItem.org_id == principal.org_id,
            InboxItem.recipient_employee_id == employee_id(principal),
            InboxItem.id == inbox_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if item is None:
        raise EmployeeRecordNotFound()
    item.read_at = item.read_at or utc_now()
    session.flush()
    return InboxPublic.model_validate(item)
