from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.authz.deps import OrgAdminRequired
from app.authz.principal import Principal
from app.domains.audit.service import record_audit
from app.domains.base import utc_now
from app.domains.change_request import employee
from app.domains.change_request.models import (
    ChangeItem,
    ChangeItemEvidence,
    ItemDecision,
    ProfileChangeRequest,
    RequestOrigin,
    RequestStatus,
    ReviewDecision,
)
from app.domains.inbox.models import InboxKind
from app.domains.inbox.service import notify
from app.domains.review.appeal_schemas import (
    AppealPublic,
    AppealReason,
    AppealResolution,
    ResolveAppealInput,
)
from app.domains.review.models import AppealStatus, ReviewAppeal


def _audit(session: Session, principal: Principal, row: ReviewAppeal) -> None:
    record_audit(
        session,
        org_id=principal.org_id,
        actor_type=principal.actor_type,
        actor_id=str(principal.user_id),
        action=f"appeal.{row.status.value}",
        target_type="review_appeal",
        target_id=str(row.id),
        details={"request_id": str(row.request_id), "item_id": str(row.item_id)},
    )


def appeal_decision(
    principal: Principal,
    session: Session,
    request_id: UUID,
    item_id: UUID,
    reason: str,
) -> AppealPublic:
    reason = AppealReason(reason=reason).reason
    request = employee._request(principal, session, request_id)
    item = session.exec(
        select(ChangeItem).where(
            ChangeItem.org_id == principal.org_id,
            ChangeItem.request_id == request.id,
            ChangeItem.id == item_id,
        )
    ).one_or_none()
    if item is None:
        raise employee.EmployeeRecordNotFound()
    if item.decision != ItemDecision.REJECTED:
        raise employee.RequestConflict("只能申诉被拒绝的改动")
    if session.exec(
        select(ReviewAppeal.id).where(
            ReviewAppeal.org_id == principal.org_id,
            ReviewAppeal.item_id == item.id,
            ReviewAppeal.status == AppealStatus.OPEN,
        )
    ).first():
        raise employee.RequestConflict("该改动已有待处理申诉")
    row = ReviewAppeal(
        org_id=principal.org_id,
        request_id=request.id,
        item_id=item.id,
        employee_id=request.subject_employee_id,
        reason=reason,
    )
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError as exc:
        if (
            getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            != "uq_open_appeal_item"
        ):
            raise
        raise employee.RequestConflict("该改动已有待处理申诉")
    _audit(session, principal, row)
    return AppealPublic.model_validate(row)


def read_appeal(
    principal: Principal, session: Session, appeal_id: UUID
) -> AppealPublic:
    row = session.exec(
        select(ReviewAppeal).where(
            ReviewAppeal.org_id == principal.org_id,
            ReviewAppeal.id == appeal_id,
        )
    ).one_or_none()
    if row is None:
        raise employee.EmployeeRecordNotFound()
    item = session.exec(
        select(ChangeItem).where(
            ChangeItem.org_id == principal.org_id,
            ChangeItem.id == row.item_id,
        )
    ).one()
    decision = session.exec(
        select(ReviewDecision).where(
            ReviewDecision.org_id == principal.org_id,
            ReviewDecision.id == item.review_decision_id,
        )
    ).one()
    if not (
        principal.is_org_admin
        or principal.employee_id == row.employee_id
        or principal.user_id is not None
        and principal.user_id
        in (decision.reviewer_user_id, decision.acting_for_user_id)
    ):
        raise employee.EmployeeRecordNotFound()
    return AppealPublic.model_validate(row)


def list_disputes(
    principal: Principal, session: Session, *, employee_id: UUID | None = None
) -> list[AppealPublic]:
    if not principal.is_org_admin:
        raise OrgAdminRequired()
    return [
        AppealPublic.model_validate(row)
        for row in session.exec(
            select(ReviewAppeal)
            .where(
                ReviewAppeal.org_id == principal.org_id,
                ReviewAppeal.status == AppealStatus.OPEN,
                *(
                    [ReviewAppeal.employee_id == employee_id]
                    if employee_id is not None
                    else []
                ),
            )
            .order_by(col(ReviewAppeal.created_at), col(ReviewAppeal.id))
        ).all()
    ]


def resolve_appeal(
    principal: Principal,
    session: Session,
    appeal_id: UUID,
    body: ResolveAppealInput,
) -> AppealResolution:
    if not principal.is_org_admin:
        raise OrgAdminRequired()
    row = session.exec(
        select(ReviewAppeal)
        .where(
            ReviewAppeal.org_id == principal.org_id,
            ReviewAppeal.id == appeal_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if row is None:
        raise employee.EmployeeRecordNotFound()
    if row.status != AppealStatus.OPEN:
        raise employee.RequestConflict("申诉已处理")
    request = session.exec(
        select(ProfileChangeRequest).where(
            ProfileChangeRequest.org_id == principal.org_id,
            ProfileChangeRequest.id == row.request_id,
        )
    ).one()
    draft = None
    if body.status == "reopened":
        original = session.exec(
            select(ChangeItem).where(
                ChangeItem.org_id == principal.org_id,
                ChangeItem.id == row.item_id,
            )
        ).one()
        draft = ProfileChangeRequest(
            org_id=principal.org_id,
            subject_employee_id=row.employee_id,
            parent_request_id=request.id,
            origin=RequestOrigin.CORRECTION,
            status=RequestStatus.DRAFT,
            author=request.author,
            base_version_id=request.base_version_id,
        )
        session.add(draft)
        session.flush()
        item = ChangeItem(
            **original.model_dump(
                exclude={
                    "id",
                    "created_at",
                    "request_id",
                    "position",
                    "decision",
                    "decision_reason",
                    "review_decision_id",
                    "published_claim_id",
                }
            ),
            request_id=draft.id,
            position=0,
        )
        session.add(item)
        session.flush()
        session.add_all(
            [
                ChangeItemEvidence(
                    org_id=principal.org_id,
                    item_id=item.id,
                    evidence_id=link.evidence_id,
                    relation=link.relation,
                )
                for link in session.exec(
                    select(ChangeItemEvidence).where(
                        ChangeItemEvidence.org_id == principal.org_id,
                        ChangeItemEvidence.item_id == original.id,
                    )
                ).all()
            ]
        )
    row.status, row.resolved_by_user_id = AppealStatus(body.status), principal.user_id
    row.resolved_at, row.resolution_note = utc_now(), body.reason
    _audit(session, principal, row)
    notify(
        session,
        draft or request,
        InboxKind.STATUS_CHANGED,
        "申诉已处理：允许重新提交，请修改草稿"
        if draft
        else "申诉已处理：维持原决定，请查看结果",
    )
    session.flush()
    return AppealResolution(
        appeal=AppealPublic.model_validate(row),
        draft_request_id=draft.id if draft else None,
    )
