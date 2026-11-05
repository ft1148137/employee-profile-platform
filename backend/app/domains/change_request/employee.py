from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from sqlmodel import Session, col, delete, select

from app.authz.principal import Principal
from app.domains.audit.service import record_audit
from app.domains.base import utc_now
from app.domains.capability.schemas import (
    ActiveSchemaPublic,
    CapabilitiesPublic,
    CapabilityPublic,
)
from app.domains.capability.service import get_schema, level_exists, list_capabilities
from app.domains.change_request.claim import ClaimAfter
from app.domains.change_request.models import (
    ChangeItem,
    ChangeItemEvidence,
    ChangeOp,
    ItemDecision,
    ProfileChangeRequest,
    RequestOrigin,
    RequestStatus,
    SubmissionVia,
)
from app.domains.change_request.schemas import (
    ChangeRequestPublic,
    ChangeRequestsPublic,
    ItemInput,
    PreviewItem,
    RequestPreview,
)
from app.domains.change_request.service import list_requests, transition
from app.domains.evidence.models import (
    Evidence,
    EvidenceKind,
    EvidenceStatus,
    Visibility,
)
from app.domains.evidence.schemas import EvidenceList
from app.domains.evidence.service import (
    append_evidence,
    evidence_access,
    list_evidence,
    retract_evidence,
)
from app.domains.inbox.models import InboxKind
from app.domains.inbox.service import notify
from app.domains.org.models import Employee, Organization
from app.domains.org.service import visible_employee
from app.domains.profile.models import AssessmentStatus, EvidenceRelation
from app.domains.profile.schemas import ClaimPublic, ProfileView
from app.domains.profile.service import read_profile

EmployeeVia = Literal["web", "mcp", "import", "bot"]


class EmployeeRecordNotFound(HTTPException):
    def __init__(self) -> None:
        super().__init__(404, "记录不存在")


class RequestConflict(HTTPException):
    def __init__(self, message: str = "当前状态不允许此操作") -> None:
        super().__init__(409, message)


class InvalidItems(HTTPException):
    def __init__(self, errors: list[dict[str, int | str]]) -> None:
        self.errors = errors
        super().__init__(422, errors)


def employee_id(principal: Principal) -> UUID:
    if principal.employee_id is None:
        raise EmployeeRecordNotFound()
    return principal.employee_id


def my_profile(principal: Principal, session: Session) -> ProfileView:
    return read_profile(session, principal, employee_id(principal))


def my_evidence(
    principal: Principal,
    session: Session,
    kind: EvidenceKind | None = None,
    include_inactive: bool = False,
    skip: int = 0,
    limit: int = 50,
) -> EvidenceList:
    return list_evidence(
        session,
        principal,
        employee_id(principal),
        kind=kind,
        include_inactive=include_inactive,
        skip=skip,
        limit=limit,
    )


def my_requests(
    principal: Principal, session: Session, status: Literal["open", "all"] = "open"
) -> ChangeRequestsPublic:
    return list_requests(session, principal, employee_id(principal), status)


def capability_dictionary(principal: Principal, session: Session) -> CapabilitiesPublic:
    visible_employee(session, principal, employee_id(principal))
    schema = get_schema(session, principal.org_id)
    return CapabilitiesPublic(
        schema_version=ActiveSchemaPublic.model_validate(schema),
        data=[
            CapabilityPublic.model_validate(c)
            for c in list_capabilities(session, principal.org_id, schema.id)
        ],
    )


def _request(
    principal: Principal, session: Session, request_id: UUID
) -> ProfileChangeRequest:
    row = session.exec(
        select(ProfileChangeRequest)
        .where(
            ProfileChangeRequest.org_id == principal.org_id,
            ProfileChangeRequest.subject_employee_id == employee_id(principal),
            ProfileChangeRequest.id == request_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if row is None:
        raise EmployeeRecordNotFound()
    return row


def _public(
    principal: Principal, session: Session, request_id: UUID
) -> ChangeRequestPublic:
    session.flush()
    return list_requests(
        session, principal, employee_id(principal), "all", request_id
    ).data[0]


def _validate(
    principal: Principal,
    session: Session,
    items: list[ItemInput],
) -> tuple[dict[str, CapabilityPublic], dict[str, ClaimPublic]]:
    capabilities = {c.key: c for c in capability_dictionary(principal, session).data}
    current = {
        i.capability.key: i.claim
        for i in my_profile(principal, session).items
        if i.claim
    }
    access = evidence_access(
        session, principal, {id_ for i in items for id_ in i.evidence_ids}
    )
    errors: list[dict[str, int | str]] = []
    seen: set[str] = set()
    for index, item in enumerate(items):
        messages = []
        capability = capabilities.get(item.capability_key)
        if capability is None:
            messages.append("能力 key 不在当前启用的词典中")
        elif item.level is not None:
            levels = (capability.level_rubric or {}).get("levels", [])
            if not isinstance(levels, list) or item.level not in {
                v.get("key") for v in levels if isinstance(v, dict)
            }:
                messages.append("等级 key 不在该能力的等级中")
        if item.capability_key in seen:
            messages.append("同一能力不能重复修改")
        seen.add(item.capability_key)
        if item.op != ChangeOp.ADD and item.capability_key not in current:
            messages.append("目标不是本人当前判断")
        if item.op == ChangeOp.ADD and item.capability_key in current:
            messages.append("该能力已有判断，请使用修改")
        if any(
            id_ not in access
            or not access[id_][1]
            or access[id_][0].subject_employee_id != principal.employee_id
            for id_ in item.evidence_ids
        ):
            messages.append("依据必须是本人可见的证据")
        errors.extend({"index": index, "message": message} for message in messages)
    if errors:
        raise InvalidItems(errors)
    return capabilities, current


def _lock_organization(principal: Principal, session: Session) -> None:
    session.exec(
        select(Organization)
        .where(Organization.id == principal.org_id)
        .with_for_update(read=True)
    ).one()


def save_draft(
    principal: Principal,
    session: Session,
    request_id: UUID | None,
    items: list[ItemInput],
    via: EmployeeVia,
    client_name: str | None = None,
) -> ChangeRequestPublic:
    _lock_organization(principal, session)
    request = _request(principal, session, request_id) if request_id else None
    if request and request.status not in (
        RequestStatus.DRAFT,
        RequestStatus.PENDING_OWNER,
    ):
        raise RequestConflict()
    capabilities, current = _validate(principal, session, items)
    if request is None:
        request = ProfileChangeRequest(
            org_id=principal.org_id,
            subject_employee_id=employee_id(principal),
            origin={
                "web": RequestOrigin.MANUAL,
                "mcp": RequestOrigin.CONVERSATION,
                "bot": RequestOrigin.CONVERSATION,
                "import": RequestOrigin.IMPORT,
            }[via],
            status=RequestStatus.DRAFT,
            author={
                "actor_type": principal.actor_type,
                "user_id": str(principal.user_id),
            },
            client_name=client_name,
        )
        session.add(request)
        session.flush()
    else:
        request.status = RequestStatus.DRAFT
        request.revision += 1
    request.updated_at = utc_now()
    old = select(ChangeItem.id).where(
        ChangeItem.org_id == principal.org_id, ChangeItem.request_id == request.id
    )
    session.exec(
        delete(ChangeItemEvidence).where(
            col(ChangeItemEvidence.org_id) == principal.org_id,
            col(ChangeItemEvidence.item_id).in_(old),
        )
    )
    session.exec(
        delete(ChangeItem).where(
            col(ChangeItem.org_id) == principal.org_id,
            col(ChangeItem.request_id) == request.id,
        )
    )
    rows = [
        ChangeItem(
            org_id=principal.org_id,
            request_id=request.id,
            position=position,
            op=item.op,
            capability_definition_id=capabilities[item.capability_key].id,
            target_claim_id=current[item.capability_key].id
            if item.op != ChangeOp.ADD
            else None,
            after=ClaimAfter(
                statement=item.statement,
                level=item.level,
                assessment_status=AssessmentStatus.TENTATIVE,
            ).model_dump(mode="json")
            if item.op in (ChangeOp.ADD, ChangeOp.MODIFY)
            else None,
            owner_statement=item.owner_statement,
            objection=item.objection,
            uncertainty=item.uncertainty,
        )
        for position, item in enumerate(items)
    ]
    session.add_all(rows)
    session.flush()
    session.add_all(
        [
            ChangeItemEvidence(
                org_id=principal.org_id,
                item_id=row.id,
                evidence_id=id_,
                relation=EvidenceRelation.SUPPORTS,
            )
            for row, item in zip(rows, items, strict=True)
            for id_ in set(item.evidence_ids)
        ]
    )
    return _public(principal, session, request.id)


def preview(principal: Principal, session: Session, request_id: UUID) -> RequestPreview:
    _lock_organization(principal, session)
    request = _request(principal, session, request_id)
    public = _public(principal, session, request.id)
    claims = {
        i.claim.id: i.claim for i in my_profile(principal, session).items if i.claim
    }
    return RequestPreview(
        request_id=request.id,
        revision=request.revision,
        items=[
            PreviewItem(
                before=claims.get(i.target_claim_id) if i.target_claim_id else None,
                after=i,
                warning="等级已调整，请重新选择或清空"
                if i.after
                and not level_exists(i.capability.level_rubric, i.after.level)
                else None,
            )
            for i in public.items
        ],
    )


def submit(
    principal: Principal,
    session: Session,
    request_id: UUID,
    revision: int,
    via: EmployeeVia,
    client_name: str | None = None,
) -> ChangeRequestPublic:
    # Match the lock order used by manager publishing and dictionary changes.
    _lock_organization(principal, session)
    request = _request(principal, session, request_id)
    session.exec(
        select(Employee)
        .where(
            Employee.org_id == principal.org_id, Employee.id == employee_id(principal)
        )
        .with_for_update()
    ).one()
    if request.revision != revision:
        raise RequestConflict("内容已变化，请重新预览")
    if request.status not in (RequestStatus.DRAFT, RequestStatus.PENDING_OWNER):
        raise RequestConflict()
    items = session.exec(
        select(ChangeItem)
        .where(
            ChangeItem.org_id == principal.org_id, ChangeItem.request_id == request.id
        )
        .order_by(col(ChangeItem.position))
    ).all()
    if not items:
        raise InvalidItems([{"index": 0, "message": "至少需要一项改动"}])
    public = _public(principal, session, request.id)
    _validate(
        principal,
        session,
        [
            ItemInput(
                op=i.op,
                capability_key=i.capability.key,
                statement=i.after.statement if i.after else "撤回或补充依据",
                level=i.after.level if i.after else None,
                evidence_ids=[e.evidence_id for e in i.evidence],
            )
            for i in public.items
        ],
    )
    transition(request, RequestStatus.PENDING_REVIEW)
    request.submitted_via, request.client_name, request.submitted_at = (
        SubmissionVia(via),
        client_name,
        utc_now(),
    )
    for item in items:
        for statement, relation in (
            (item.owner_statement, EvidenceRelation.SUPPORTS),
            (item.objection, EvidenceRelation.CONTRADICTS),
        ):
            if not statement:
                continue
            evidence, _ = append_evidence(
                session,
                org_id=principal.org_id,
                subject_employee_id=employee_id(principal),
                kind=EvidenceKind.SELF_REPORT,
                source=f"self_report:{via}",
                source_ref=str(item.id),
                title="员工依据说明"
                if relation == EvidenceRelation.SUPPORTS
                else "员工异议",
                content={"statement": statement, "relation": relation},
                visibility=Visibility.RESTRICTED,
                provenance={
                    "via": via,
                    "client_name": client_name,
                    "request_id": str(request.id),
                    **(
                        {"imported_by": request.author["user_id"]}
                        if request.origin == RequestOrigin.IMPORT
                        and request.author.get("user_id")
                        and request.author["user_id"] != str(principal.user_id)
                        else {}
                    ),
                },
            )
            session.add(
                ChangeItemEvidence(
                    org_id=principal.org_id,
                    item_id=item.id,
                    evidence_id=evidence.id,
                    relation=relation,
                )
            )
    record_audit(
        session,
        org_id=principal.org_id,
        actor_type=principal.actor_type,
        actor_id=str(principal.user_id),
        action="change_request.submitted",
        target_type="profile_change_request",
        target_id=str(request.id),
        details={"via": via, "client_name": client_name, "revision": revision},
    )
    from app.domains.review.policy import apply_policy

    session.flush()
    apply_policy(session, principal, request, list(items))
    message = (
        "请求已按审批规则发布为暂定，请查看逐项结果"
        if all(i.decision == ItemDecision.APPROVED for i in items)
        else "部分改动已按审批规则发布为暂定，其余改动等待经理审批"
        if any(i.decision == ItemDecision.APPROVED for i in items)
        else (
            f"你通过 {client_name or via} 提交了一个请求，经理批准前可以撤回"
            if via in ("mcp", "import")
            else "请求已提交，经理批准前可以撤回"
        )
    )
    notify(session, request, InboxKind.SUBMITTED, message)
    from app.domains.review.routing import notify_reviewers

    if any(i.decision is None for i in items):
        notify_reviewers(session, request)
    return _public(principal, session, request.id)


def withdraw(
    principal: Principal, session: Session, request_id: UUID
) -> ChangeRequestPublic:
    request = _request(principal, session, request_id)
    if request.status not in (RequestStatus.DRAFT, RequestStatus.PENDING_REVIEW):
        raise RequestConflict()
    transition(request, RequestStatus.WITHDRAWN)
    published_items = {
        str(id_)
        for id_ in session.exec(
            select(ChangeItem.id).where(
                ChangeItem.org_id == principal.org_id,
                ChangeItem.request_id == request.id,
                ChangeItem.decision == ItemDecision.APPROVED,
            )
        ).all()
    }
    records = session.exec(
        select(Evidence)
        .where(
            Evidence.org_id == principal.org_id,
            Evidence.subject_employee_id == employee_id(principal),
            Evidence.kind == EvidenceKind.SELF_REPORT,
            Evidence.source == f"self_report:{request.submitted_via}",
            col(Evidence.provenance)["request_id"].astext == str(request.id),
            Evidence.status == EvidenceStatus.ACTIVE,
        )
        .order_by(col(Evidence.id))
    ).all()
    for evidence in records:
        if evidence.source_ref in published_items:
            continue
        retract_evidence(
            session,
            evidence=evidence,
            reason="员工撤回请求",
            actor_type=principal.actor_type,
            actor_id=str(principal.user_id),
        )
    record_audit(
        session,
        org_id=principal.org_id,
        actor_type=principal.actor_type,
        actor_id=str(principal.user_id),
        action="change_request.withdrawn",
        target_type="profile_change_request",
        target_id=str(request.id),
        details={},
    )
    notify(session, request, InboxKind.STATUS_CHANGED, "请求已撤回")
    return _public(principal, session, request.id)
