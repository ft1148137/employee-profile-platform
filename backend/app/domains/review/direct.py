from typing import Literal
from uuid import UUID, uuid7
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import JsonValue
from sqlmodel import Field, Session, SQLModel, col, select

from app.authz.principal import Principal
from app.domains.audit.service import record_audit
from app.domains.base import utc_now
from app.domains.capability.service import get_schema, level_exists, list_capabilities
from app.domains.change_request.claim import ClaimAfter
from app.domains.change_request.employee import (
    EmployeeRecordNotFound,
    InvalidItems,
    RequestConflict,
    _lock_organization,
)
from app.domains.change_request.models import (
    ChangeItem,
    ChangeOp,
    ItemDecision,
    ProfileChangeRequest,
    RequestOrigin,
    RequestStatus,
    ReviewDecision,
    ReviewVia,
    SubmissionVia,
)
from app.domains.change_request.schemas import ToolInput
from app.domains.change_request.service import transition
from app.domains.inbox.models import InboxKind
from app.domains.inbox.service import notify
from app.domains.org.models import Employee, EmploymentStatus
from app.domains.org.service import visible_employee
from app.domains.profile.models import AssessmentStatus, ProfileVersion
from app.domains.review.publish import current_claims, publish
from app.models import User


class DirectItemInput(ToolInput):
    op: Literal[ChangeOp.ADD, ChangeOp.MODIFY, ChangeOp.RETRACT]
    capability_key: str
    statement: str = Field(min_length=1)
    level: str | None = None
    explanation: str | None = Field(default=None, max_length=300)


class DirectEditInput(ToolInput):
    items: list[DirectItemInput] = Field(min_length=1, max_length=50)
    base_revision: int | None = Field(default=None, ge=1)


class DirectClaim(SQLModel):
    statement: str
    level: str | None
    assessment_status: AssessmentStatus


class DirectEditItem(SQLModel):
    capability_key: str
    capability_name: str
    before: DirectClaim | None
    after: DirectClaim


class DirectEditResult(SQLModel):
    revision: int | None
    items: list[DirectEditItem]


class SelfDirectEditForbidden(HTTPException):
    def __init__(self) -> None:
        super().__init__(403, "不能直接修改自己的画像，请在画像页提交修改")


def _subject(principal: Principal, session: Session, employee_id: UUID) -> Employee:
    if principal.employee_id == employee_id:
        raise SelfDirectEditForbidden()
    if principal.user_id is None or not (
        principal.is_org_admin or principal.managed_department_ids
    ):
        raise EmployeeRecordNotFound()
    subject = visible_employee(session, principal, employee_id)
    if subject.employment_status != EmploymentStatus.ACTIVE:
        raise EmployeeRecordNotFound()
    return subject


def _prepare(
    principal: Principal,
    session: Session,
    employee_id: UUID,
    body: DirectEditInput,
    request_id: UUID,
) -> tuple[ProfileVersion | None, list[ChangeItem], DirectEditResult]:
    previous = session.exec(
        select(ProfileVersion)
        .where(
            ProfileVersion.org_id == principal.org_id,
            ProfileVersion.subject_employee_id == employee_id,
        )
        .order_by(col(ProfileVersion.revision).desc())
    ).first()
    revision = previous.revision if previous else None
    if body.base_revision != revision:
        raise RequestConflict("画像已变化，请重新查看")
    definitions = {
        c.key: c
        for c in list_capabilities(
            session, principal.org_id, get_schema(session, principal.org_id).id
        )
    }
    current = current_claims(session, principal.org_id, employee_id)
    errors: list[dict[str, int | str]] = []
    seen: set[str] = set()
    rows, previews = [], []
    for index, item in enumerate(body.items):
        capability = definitions.get(item.capability_key)
        target = current.get(item.capability_key)
        messages = []
        if capability is None:
            messages.append("能力 key 不在当前启用的词典中")
        elif not level_exists(capability.level_rubric, item.level):
            messages.append("等级 key 不在该能力的等级中")
        if item.capability_key in seen:
            messages.append("同一能力不能重复修改")
        seen.add(item.capability_key)
        if item.op == ChangeOp.ADD and target is not None:
            messages.append("该能力已有判断，请使用修改")
        if item.op != ChangeOp.ADD and target is None:
            messages.append("目标不是当前判断")
        errors.extend({"index": index, "message": message} for message in messages)
        if messages or capability is None:
            continue
        after = DirectClaim(
            statement=target.statement
            if item.op == ChangeOp.RETRACT and target
            else item.statement,
            level=None if item.op == ChangeOp.RETRACT else item.level,
            assessment_status=AssessmentStatus.UNKNOWN
            if item.op == ChangeOp.RETRACT
            else AssessmentStatus.CONFIRMED,
        )
        rows.append(
            ChangeItem(
                org_id=principal.org_id,
                request_id=request_id,
                position=index,
                op=item.op,
                capability_definition_id=capability.id,
                target_claim_id=target.id if target else None,
                explanation=item.explanation,
                after=ClaimAfter.model_validate(after.model_dump()).model_dump(
                    mode="json"
                )
                if item.op != ChangeOp.RETRACT
                else None,
            )
        )
        previews.append(
            DirectEditItem(
                capability_key=item.capability_key,
                capability_name=capability.name,
                before=DirectClaim.model_validate(target) if target else None,
                after=after,
            )
        )
    if errors:
        raise InvalidItems(errors)
    return previous, rows, DirectEditResult(revision=revision, items=previews)


def preview_direct_edit(
    principal: Principal, session: Session, employee_id: UUID, body: DirectEditInput
) -> DirectEditResult:
    _subject(principal, session, employee_id)
    return _prepare(principal, session, employee_id, body, uuid7())[2]


def direct_edit(
    principal: Principal,
    session: Session,
    employee_id: UUID,
    body: DirectEditInput,
    *,
    via: Literal["web", "bot"],
    client_name: str,
    commit: bool = True,
) -> DirectEditResult:
    _subject(principal, session, employee_id)
    _lock_organization(principal, session)
    session.exec(
        select(Employee)
        .where(
            Employee.org_id == principal.org_id,
            Employee.id == employee_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    _subject(principal, session, employee_id)
    request_id = uuid7()
    previous, items, result = _prepare(
        principal, session, employee_id, body, request_id
    )
    now = utc_now()
    request = ProfileChangeRequest(
        id=request_id,
        org_id=principal.org_id,
        subject_employee_id=employee_id,
        origin=RequestOrigin.MANUAL,
        status=RequestStatus.DRAFT,
        author={"actor_type": principal.actor_type, "user_id": str(principal.user_id)},
        base_version_id=previous.id if previous else None,
        submitted_via=SubmissionVia(via),
        client_name=client_name,
        submitted_at=now,
    )
    session.add(request)
    session.flush()
    transition(request, RequestStatus.PENDING_REVIEW)
    decision = ReviewDecision(
        org_id=principal.org_id,
        request_id=request.id,
        reviewer_user_id=principal.user_id,
        via=ReviewVia.DIRECT,
        client_name=client_name,
        request_revision=request.revision,
    )
    session.add(decision)
    session.flush()
    for item in items:
        item.decision, item.review_decision_id = ItemDecision.APPROVED, decision.id
        session.add(item)
    session.flush()
    version = publish(session, request, items, decision)
    transition(request, RequestStatus.APPROVED)
    request.decided_at = now
    result.revision = version.revision
    details: dict[str, JsonValue] = {
        "employee_id": str(employee_id),
        "role": "org_admin" if principal.is_org_admin else "dept_manager",
        "via": via,
        "client_name": client_name,
        "items": [i.model_dump(mode="json") for i in result.items],
        "revision": version.revision,
    }
    record_audit(
        session,
        org_id=principal.org_id,
        actor_type=principal.actor_type,
        actor_id=str(principal.user_id),
        action="profile.direct_edited",
        target_type="employee",
        target_id=str(employee_id),
        details=details,
    )
    actor = session.get(User, principal.user_id)
    assert actor is not None
    names = "、".join(i.capability_name for i in result.items)
    # shortcut: 按北京时间显示，企业有时区设置后改用企业时区。
    display_time = now.astimezone(ZoneInfo("Asia/Shanghai"))
    notify(
        session,
        request,
        InboxKind.PROFILE_EDITED,
        f"{actor.full_name or actor.email}于 {display_time:%Y-%m-%d %H:%M} 直接修改了你的画像：{names}。如有异议，可以在画像页修改后提交经理审批",
    )
    session.flush()
    if commit:
        session.commit()
    return result
