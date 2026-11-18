from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlmodel import Session, col, select

from app.authz.principal import Principal
from app.domains.capability.service import get_schema, level_exists, list_capabilities
from app.domains.change_request.claim import ITEMS_MAX, ClaimAfter
from app.domains.change_request.models import (
    ChangeItem,
    ChangeItemEvidence,
    ChangeOp,
    ProfileChangeRequest,
    RequestOrigin,
    RequestStatus,
)
from app.domains.evidence.models import (
    ASSIGNMENT_FEEDBACK_SOURCE,
    Evidence,
    EvidenceKind,
    EvidenceStatus,
    Visibility,
)
from app.domains.evidence.service import append_evidence
from app.domains.inbox.models import InboxKind
from app.domains.inbox.service import notify
from app.domains.profile.models import (
    AssessmentStatus,
    EvidenceRelation,
    ProfileVersion,
)
from app.domains.review.publish import current_claims


class FeedbackInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=20, max_length=500)
    ]
    capability_keys: list[str] = Field(min_length=1, max_length=ITEMS_MAX)


class FeedbackConflict(HTTPException):
    def __init__(self, detail: str) -> None:
        super().__init__(409, detail)


class FeedbackInvalid(HTTPException):
    def __init__(self) -> None:
        super().__init__(422, "请选择当前能力词典中的能力")


def recorded_feedback(
    session: Session, org_id: UUID, assignment_ids: list[UUID]
) -> set[str]:
    if not assignment_ids:
        return set()
    return set(
        session.exec(
            select(Evidence.source_ref).where(
                Evidence.org_id == org_id,
                Evidence.source == ASSIGNMENT_FEEDBACK_SOURCE,
                col(Evidence.source_ref).in_([str(id_) for id_ in assignment_ids]),
                Evidence.status == EvidenceStatus.ACTIVE,
            )
        )
    )


def create_feedback(
    principal: Principal,
    session: Session,
    body: FeedbackInput,
    *,
    assignment_id: UUID,
    employee_id: UUID,
    closed_at: datetime | None,
    levels: dict[str, str | None],
) -> UUID:
    # The route holds the assignment lock across this check and the commit.
    org_id = principal.org_id
    if recorded_feedback(session, org_id, [assignment_id]):
        raise FeedbackConflict("该分配已有工作反馈，请通过证据更正流程纠错")
    schema = get_schema(session, org_id)
    definitions = {c.key: c for c in list_capabilities(session, org_id, schema.id)}
    keys = list(dict.fromkeys(body.capability_keys))
    if any(key not in definitions for key in keys):
        raise FeedbackInvalid()
    evidence, created = append_evidence(
        session,
        org_id=org_id,
        subject_employee_id=employee_id,
        kind=EvidenceKind.MANAGER_REVIEW,
        source=ASSIGNMENT_FEEDBACK_SOURCE,
        source_ref=str(assignment_id),
        title="工作反馈",
        content={"comment": body.text},
        visibility=Visibility.RESTRICTED,
        occurred_at=closed_at,
        provenance={"user_id": str(principal.user_id)},
    )
    if not created:
        raise FeedbackConflict("该分配已有相同反馈，请通过证据更正流程纠错")
    base = session.exec(
        select(ProfileVersion.id)
        .where(
            ProfileVersion.org_id == org_id,
            ProfileVersion.subject_employee_id == employee_id,
        )
        .order_by(col(ProfileVersion.revision).desc())
    ).first()
    request = ProfileChangeRequest(
        org_id=org_id,
        subject_employee_id=employee_id,
        origin=RequestOrigin.ASSIGNMENT_FEEDBACK,
        status=RequestStatus.PENDING_OWNER,
        author={"actor_type": principal.actor_type, "user_id": str(principal.user_id)},
        base_version_id=base,
    )
    session.add(request)
    session.flush()
    claims = current_claims(session, org_id, employee_id)
    for position, key in enumerate(keys):
        target = claims.get(key)
        capability = definitions[key]
        level = levels.get(key)
        item = ChangeItem(
            org_id=org_id,
            request_id=request.id,
            position=position,
            capability_definition_id=capability.id,
            op=ChangeOp.ATTACH_EVIDENCE if target else ChangeOp.ADD,
            target_claim_id=target.id if target else None,
            after=None
            if target
            else ClaimAfter(
                statement="已完成与该能力相关的分配工作",
                level=level if level_exists(capability.level_rubric, level) else None,
                assessment_status=AssessmentStatus.TENTATIVE,
            ).model_dump(mode="json"),
        )
        session.add(item)
        session.flush()
        session.add(
            ChangeItemEvidence(
                org_id=org_id,
                item_id=item.id,
                evidence_id=evidence.id,
                relation=EvidenceRelation.SUPPORTS,
            )
        )
    notify(
        session, request, InboxKind.INITIAL_REVIEW, "你收到一条工作反馈，请过目后提交"
    )
    return request.id
