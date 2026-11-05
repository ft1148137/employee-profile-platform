from datetime import timedelta

from sqlmodel import Session, col, select

from app.authz.principal import Principal
from app.domains.audit.service import record_audit
from app.domains.base import utc_now
from app.domains.change_request.models import (
    ChangeItem,
    ProfileChangeRequest,
    RequestOrigin,
    RequestStatus,
    SubmissionVia,
)
from app.domains.change_request.service import transition
from app.domains.drafting.settings import drafting_scope
from app.domains.inbox.models import InboxKind
from app.domains.inbox.service import notify
from app.domains.review.policy import read_policy
from app.domains.review.routing import notify_reviewers


def hand_over(session: Session, principal: Principal, *, confirm: bool = False) -> int:
    owner_review_days = read_policy(session, principal.org_id).owner_review_days
    scope = drafting_scope(session, principal.org_id)
    query = (
        select(ProfileChangeRequest)
        .where(
            ProfileChangeRequest.org_id == principal.org_id,
            ProfileChangeRequest.origin == RequestOrigin.INITIAL_SYNC,
            ProfileChangeRequest.status == RequestStatus.PENDING_OWNER,
            ProfileChangeRequest.created_at
            <= utc_now() - timedelta(days=owner_review_days),
            col(ProfileChangeRequest.subject_employee_id).not_in(
                scope.excluded_employee_ids
            ),
            select(ChangeItem.id)
            .where(
                ChangeItem.org_id == principal.org_id,
                ChangeItem.request_id == ProfileChangeRequest.id,
            )
            .exists(),
        )
        .order_by(col(ProfileChangeRequest.id))
    )
    if confirm:
        query = query.with_for_update().execution_options(populate_existing=True)
    requests = session.exec(query).all()
    if confirm:
        for request in requests:
            transition(request, RequestStatus.PENDING_REVIEW)
            request.submitted_via = SubmissionVia.ADMIN_HANDOVER
            request.submitted_at = utc_now()
            notify_reviewers(session, request)
            record_audit(
                session,
                org_id=principal.org_id,
                actor_type=principal.actor_type,
                actor_id=str(principal.user_id),
                action="initial_profiles.handed_over",
                target_type="profile_change_request",
                target_id=str(request.id),
                details={"via": "admin_handover", "owner_reviewed": False},
            )
            notify(
                session,
                request,
                InboxKind.SUBMITTED,
                f"初始画像满 {owner_review_days} 天未处理，管理员已送交经理审批（员工未过目），批准前你可以撤回",
            )
        session.commit()
    return len(requests)
