from uuid import UUID

from fastapi import HTTPException
from sqlmodel import Session, col, select

from app.authz.principal import Principal
from app.authz.review import review_access
from app.domains.audit.service import record_audit
from app.domains.base import utc_now
from app.domains.capability.service import get_schema, level_exists, list_capabilities
from app.domains.change_request.employee import (
    EmployeeRecordNotFound,
    InvalidItems,
    RequestConflict,
)
from app.domains.change_request.models import (
    ChangeItem,
    ChangeItemEvidence,
    ItemDecision,
    ProfileChangeRequest,
    RequestOrigin,
    RequestStatus,
    ReviewDecision,
    ReviewVia,
    SubmissionVia,
)
from app.domains.change_request.service import list_requests, transition
from app.domains.evidence.models import EvidenceKind, Visibility
from app.domains.evidence.service import append_evidence, read_evidence
from app.domains.inbox.models import InboxKind
from app.domains.inbox.service import notify
from app.domains.org.models import Department, Employee, Organization, OrgMembership
from app.domains.org.service import visible_employee
from app.domains.profile.models import EvidenceRelation
from app.domains.profile.schemas import ClaimPublic
from app.domains.review.publish import current_claims, item_evidence, jira_only, publish
from app.domains.review.routing import reviewers_for
from app.domains.review.schemas import (
    DecisionInput,
    DecisionResult,
    PendingReviews,
    ReviewDetail,
    ReviewItem,
    ReviewSummary,
)


class ReviewForbidden(HTTPException):
    def __init__(self) -> None:
        super().__init__(403, "你不是当前审批人，或不能审批本人的请求")


def list_pending_reviews(principal: Principal, session: Session) -> PendingReviews:
    data = []
    for request, subject, department in session.exec(
        select(ProfileChangeRequest, Employee, Department)
        .join(
            Employee,
            (col(Employee.id) == ProfileChangeRequest.subject_employee_id)
            & (col(Employee.org_id) == ProfileChangeRequest.org_id),
        )
        .outerjoin(
            Department,
            (col(Department.id) == Employee.department_id)
            & (col(Department.org_id) == Employee.org_id),
        )
        .where(
            ProfileChangeRequest.org_id == principal.org_id,
            ProfileChangeRequest.status == RequestStatus.PENDING_REVIEW,
        )
        .order_by(col(ProfileChangeRequest.submitted_at), col(ProfileChangeRequest.id))
    ).all():
        reviewers = reviewers_for(session, principal.org_id, subject.id)
        allowed = review_access(session, principal, request)
        if not allowed and not (principal.is_org_admin and not reviewers):
            continue
        visible_employee(session, principal, subject.id)
        count = len(
            session.exec(
                select(ChangeItem.id).where(
                    ChangeItem.org_id == principal.org_id,
                    ChangeItem.request_id == request.id,
                )
            ).all()
        )
        data.append(
            ReviewSummary(
                id=request.id,
                subject_employee_id=subject.id,
                employee_name=subject.display_name,
                department_name=department.name if department else None,
                submitted_at=request.submitted_at,
                submitted_via=request.submitted_via,
                client_name=request.client_name,
                owner_reviewed=request.submitted_via != SubmissionVia.ADMIN_HANDOVER,
                item_count=count,
                can_decide=allowed,
                unavailable_reason=None if reviewers else "没有可用的审批人",
            )
        )
    return PendingReviews(data=data, count=len(data))


def _request(
    principal: Principal, session: Session, request_id: UUID, *, lock: bool = False
) -> ProfileChangeRequest:
    query = select(ProfileChangeRequest).where(
        ProfileChangeRequest.org_id == principal.org_id,
        ProfileChangeRequest.id == request_id,
    )
    if lock:
        query = query.with_for_update()
    request = session.exec(
        query.execution_options(populate_existing=True)
    ).one_or_none()
    if request is None:
        raise EmployeeRecordNotFound()
    if principal.employee_id == request.subject_employee_id:
        raise ReviewForbidden()
    return request


def get_review(
    principal: Principal, session: Session, request_id: UUID
) -> ReviewDetail:
    request = _request(principal, session, request_id)
    if not review_access(session, principal, request):
        raise ReviewForbidden()
    public = list_requests(
        session, principal, request.subject_employee_id, "all", request.id
    ).data[0]
    schema = get_schema(session, principal.org_id)
    definitions = {
        c.key: c for c in list_capabilities(session, principal.org_id, schema.id)
    }
    current = current_claims(session, principal.org_id, request.subject_employee_id)
    rows = {
        i.id: i
        for i in session.exec(
            select(ChangeItem).where(
                ChangeItem.org_id == principal.org_id,
                ChangeItem.request_id == request.id,
            )
        ).all()
    }
    result = []
    for item in public.items:
        before = current.get(item.capability.key)
        definition = definitions.get(item.capability.key)
        reason = (
            "能力 key 不在当前启用的词典中"
            if definition is None
            else "目标判断已被其他请求取代"
            if (before.id if before else None) != item.target_claim_id
            else "等级已调整，请退回重新选择"
            if item.after
            and not level_exists(definition.level_rubric, item.after.level)
            else None
        )
        result.append(
            ReviewItem(
                before=ClaimPublic.model_validate(before) if before else None,
                after=item,
                evidence=[
                    read_evidence(session, principal, id_)
                    for id_ in item_evidence(session, rows[item.id])
                ],
                jira_only=jira_only(item_evidence(session, rows[item.id])),
                approvable=reason is None,
                approval_blocked_reason=reason,
            )
        )
    return ReviewDetail(id=request.id, revision=request.revision, items=result)


def decide(
    principal: Principal,
    session: Session,
    request_id: UUID,
    revision: int,
    decisions: list[DecisionInput],
    comment: str | None = None,
    via: ReviewVia = ReviewVia.WEB,
    client_name: str | None = None,
) -> DecisionResult:
    # Dictionary saves take the exclusive organization lock before remapping requests.
    session.exec(
        select(Organization)
        .where(Organization.id == principal.org_id)
        .with_for_update(read=True)
    ).one()
    request = _request(principal, session, request_id, lock=True)
    if request.revision != revision or request.status != RequestStatus.PENDING_REVIEW:
        raise RequestConflict("内容已变化，请重新查看")
    if via not in (ReviewVia.WEB, ReviewVia.MCP, ReviewVia.IM) or not review_access(
        session, principal, request
    ):
        raise ReviewForbidden()
    session.exec(
        select(Employee)
        .where(
            Employee.org_id == principal.org_id,
            Employee.id == request.subject_employee_id,
        )
        .with_for_update()
    ).one()
    preview = get_review(principal, session, request.id)
    rows = session.exec(
        select(ChangeItem)
        .where(
            ChangeItem.org_id == principal.org_id, ChangeItem.request_id == request.id
        )
        .order_by(col(ChangeItem.position))
    ).all()
    inputs = {d.item_id: d for d in decisions}
    if len(inputs) != len(decisions) or set(inputs) != {
        i.id for i in rows if i.decision is None
    }:
        raise InvalidItems(
            [{"index": 0, "message": "必须逐项决定所有尚未决定的改动，不能重复"}]
        )
    for index, review_item in enumerate(preview.items):
        value = inputs[review_item.after.id]
        if value.decision != ItemDecision.APPROVED and not (value.reason or "").strip():
            raise InvalidItems([{"index": index, "message": "退回和拒绝必须填写原因"}])
        if value.decision == ItemDecision.APPROVED and not review_item.approvable:
            raise InvalidItems(
                [
                    {
                        "index": index,
                        "message": review_item.approval_blocked_reason or "不能批准",
                    }
                ]
            )
    member = session.exec(
        select(OrgMembership).where(
            OrgMembership.org_id == principal.org_id,
            OrgMembership.user_id == principal.user_id,
        )
    ).one()
    decision = ReviewDecision(
        org_id=principal.org_id,
        request_id=request.id,
        reviewer_membership_id=member.id,
        via=via,
        client_name=client_name,
        comment=comment,
        request_revision=revision,
    )
    session.add(decision)
    session.flush()
    for item in rows:
        value = inputs[item.id]
        item.decision, item.decision_reason, item.review_decision_id = (
            value.decision,
            value.reason,
            decision.id,
        )
    approved = [i for i in rows if i.decision == ItemDecision.APPROVED]
    returned = [i for i in rows if i.decision == ItemDecision.CHANGES_REQUESTED]
    version = publish(session, request, approved, decision) if approved else None
    draft = None
    if returned:
        draft = ProfileChangeRequest(
            org_id=request.org_id,
            subject_employee_id=request.subject_employee_id,
            parent_request_id=request.id,
            origin=RequestOrigin.CORRECTION,
            status=RequestStatus.DRAFT,
            author=request.author,
            base_version_id=version.id if version else request.base_version_id,
        )
        session.add(draft)
        session.flush()
        for position, original in enumerate(returned):
            item = ChangeItem(
                **original.model_dump(
                    exclude={
                        "id",
                        "created_at",
                        "request_id",
                        "position",
                        "decision",
                        "review_decision_id",
                        "published_claim_id",
                    }
                ),
                request_id=draft.id,
                position=position,
            )
            session.add(item)
            session.flush()
            session.add_all(
                [
                    ChangeItemEvidence(
                        org_id=request.org_id,
                        item_id=item.id,
                        evidence_id=id_,
                        relation=relation,
                    )
                    for id_, (_, relation) in item_evidence(session, original).items()
                ]
            )
        notify(
            session,
            draft,
            InboxKind.STATUS_CHANGED,
            "部分改动已退回，请修改草稿后重新提交",
        )
    status = (
        RequestStatus.APPROVED
        if approved
        else RequestStatus.CHANGES_REQUESTED
        if returned
        else RequestStatus.REJECTED
    )
    transition(request, status)
    request.decided_at = utc_now()
    reason_items = [i for i in rows if (i.decision_reason or "").strip()]
    if (comment or "").strip() or reason_items:
        evidence, _ = append_evidence(
            session,
            org_id=request.org_id,
            subject_employee_id=request.subject_employee_id,
            kind=EvidenceKind.MANAGER_REVIEW,
            source="manager_review",
            source_ref=str(decision.id),
            title="画像审批意见",
            content={
                "comment": comment,
                "reasons": [
                    {"item_id": str(i.id), "reason": i.decision_reason}
                    for i in reason_items
                ],
            },
            visibility=Visibility.RESTRICTED,
            provenance={
                "request_id": str(request.id),
                "decision_id": str(decision.id),
                "via": via.value,
            },
        )
        session.add_all(
            [
                ChangeItemEvidence(
                    org_id=request.org_id,
                    item_id=i.id,
                    evidence_id=evidence.id,
                    relation=EvidenceRelation.CONTEXT,
                )
                for i in (list(rows) if (comment or "").strip() else reason_items)
            ]
        )
    record_audit(
        session,
        org_id=request.org_id,
        actor_type=principal.actor_type,
        actor_id=str(principal.user_id),
        action="change_request.decided",
        target_type="profile_change_request",
        target_id=str(request.id),
        details={
            "decision_id": str(decision.id),
            "via": via.value,
            "client_name": client_name,
        },
    )
    notify(session, request, InboxKind.DECIDED, "画像请求已完成审批，请查看逐项结果")
    session.flush()
    return DecisionResult(
        request_id=request.id,
        decision_id=decision.id,
        status=request.status,
        revision=request.revision,
        published_version_id=version.id if version else None,
        returned_request_id=draft.id if draft else None,
    )
