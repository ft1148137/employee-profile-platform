from datetime import datetime
from uuid import UUID

from fastapi import HTTPException
from pydantic import AwareDatetime, model_validator
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, col, select

from app.authz.principal import Principal
from app.domains.audit.service import record_audit
from app.domains.base import utc_now
from app.domains.change_request.schemas import ToolInput
from app.domains.org.models import OrgMembership
from app.domains.review.models import ReviewDelegation
from app.models import User


class DelegationForbidden(HTTPException):
    def __init__(self) -> None:
        super().__init__(403, "只有部门经理可以设置本人的代理审批人")


class DelegationInvalid(HTTPException):
    def __init__(self) -> None:
        super().__init__(422, "请选择同企业启用的其他成员，并设置尚未结束的有效时段")


class DelegationConflict(HTTPException):
    def __init__(self) -> None:
        super().__init__(409, "代理时段重叠，请先撤销已有代理")


class DelegationNotFound(HTTPException):
    def __init__(self) -> None:
        super().__init__(404, "未找到本人的代理设置")


class DelegationInput(ToolInput):
    delegate_user_id: UUID
    starts_at: AwareDatetime
    ends_at: AwareDatetime

    @model_validator(mode="after")
    def dates(self) -> DelegationInput:
        if self.starts_at >= self.ends_at:
            raise ValueError("结束时间必须晚于开始时间")
        return self


class DelegateCandidate(SQLModel):
    user_id: UUID
    name: str


class DelegationPublic(SQLModel):
    id: UUID
    delegate_user_id: UUID
    delegate_name: str
    starts_at: datetime
    ends_at: datetime
    revoked_at: datetime | None


class DelegationView(SQLModel):
    data: list[DelegationPublic]
    candidates: list[DelegateCandidate]


def delegation_view(session: Session, principal: Principal) -> DelegationView:
    rows = session.exec(
        select(ReviewDelegation, User)
        .join(User, col(User.id) == ReviewDelegation.delegate_user_id)
        .where(
            ReviewDelegation.org_id == principal.org_id,
            ReviewDelegation.manager_user_id == principal.user_id,
        )
        .order_by(col(ReviewDelegation.starts_at).desc())
    ).all()
    candidates = (
        session.exec(
            select(User)
            .join(OrgMembership, col(OrgMembership.user_id) == User.id)
            .where(
                OrgMembership.org_id == principal.org_id,
                User.is_active,
                User.id != principal.user_id,
            )
            .order_by(col(User.username))
        ).all()
        if principal.managed_department_ids
        else []
    )
    return DelegationView(
        data=[
            DelegationPublic.model_validate(
                row, update={"delegate_name": user.full_name or user.username}
            )
            for row, user in rows
        ],
        candidates=[
            DelegateCandidate(user_id=user.id, name=user.full_name or user.username)
            for user in candidates
        ],
    )


def create_delegation(
    session: Session, principal: Principal, body: DelegationInput
) -> None:
    if not principal.managed_department_ids:
        raise DelegationForbidden()
    member = session.exec(
        select(OrgMembership)
        .join(User, col(User.id) == OrgMembership.user_id)
        .where(
            OrgMembership.org_id == principal.org_id,
            OrgMembership.user_id == body.delegate_user_id,
            User.is_active,
        )
    ).one_or_none()
    if (
        member is None
        or body.delegate_user_id == principal.user_id
        or body.ends_at <= utc_now()
    ):
        raise DelegationInvalid()
    assert principal.user_id is not None
    row = ReviewDelegation(
        org_id=principal.org_id,
        manager_user_id=principal.user_id,
        created_by_user_id=principal.user_id,
        **body.model_dump(),
    )
    session.add(row)
    try:
        session.flush()
    except IntegrityError as error:
        session.rollback()
        if getattr(error.orig, "sqlstate", None) == "23P01":
            raise DelegationConflict() from error
        raise
    audit_delegation(session, principal, row, "created")


def revoke_delegation(
    session: Session, principal: Principal, delegation_id: UUID
) -> None:
    row = session.exec(
        select(ReviewDelegation)
        .where(
            ReviewDelegation.org_id == principal.org_id,
            ReviewDelegation.manager_user_id == principal.user_id,
            ReviewDelegation.id == delegation_id,
        )
        .with_for_update()
    ).one_or_none()
    if row is None:
        raise DelegationNotFound()
    if row.revoked_at is None:
        row.revoked_at = utc_now()
        audit_delegation(session, principal, row, "revoked")


def audit_delegation(
    session: Session, principal: Principal, row: ReviewDelegation, action: str
) -> None:
    record_audit(
        session,
        org_id=principal.org_id,
        actor_type=principal.actor_type,
        actor_id=str(principal.user_id),
        action=f"review_delegation.{action}",
        target_type="review_delegation",
        target_id=str(row.id),
        details={
            "delegate_user_id": str(row.delegate_user_id),
            "starts_at": row.starts_at.isoformat(),
            "ends_at": row.ends_at.isoformat(),
        },
    )
