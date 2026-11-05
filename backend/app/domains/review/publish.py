from uuid import UUID

from pydantic import JsonValue
from sqlmodel import Session, col, select

from app.domains.base import utc_now
from app.domains.capability.models import CapabilityDefinition
from app.domains.capability.service import get_schema, list_capabilities
from app.domains.change_request.claim import ClaimAfter
from app.domains.change_request.employee import RequestConflict
from app.domains.change_request.models import (
    ChangeItem,
    ChangeItemEvidence,
    ChangeOp,
    ProfileChangeRequest,
    ReviewDecision,
    ReviewVia,
)
from app.domains.evidence.models import Evidence
from app.domains.profile.models import (
    AssessmentStatus,
    ClaimEvidence,
    EvidenceRelation,
    ProfileClaim,
    ProfileVersion,
)
from app.domains.profile.service import build_profile_snapshot


def current_claims(
    session: Session, org_id: UUID, employee_id: UUID
) -> dict[str, ProfileClaim]:
    rows = session.exec(
        select(ProfileClaim, CapabilityDefinition.key)
        .join(
            CapabilityDefinition,
            (col(CapabilityDefinition.id) == ProfileClaim.capability_definition_id)
            & (col(CapabilityDefinition.org_id) == ProfileClaim.org_id),
        )
        .where(
            ProfileClaim.org_id == org_id,
            ProfileClaim.subject_employee_id == employee_id,
        )
        .order_by(col(ProfileClaim.created_at), col(ProfileClaim.id))
    ).all()
    superseded = {c.supersedes_claim_id for c, _ in rows}
    return {key: c for c, key in rows if c.id not in superseded}


def item_evidence(
    session: Session, item: ChangeItem
) -> dict[UUID, tuple[Evidence, EvidenceRelation]]:
    return {
        e.id: (e, link.relation)
        for link, e in session.exec(
            select(ChangeItemEvidence, Evidence)
            .join(
                Evidence,
                (col(Evidence.id) == ChangeItemEvidence.evidence_id)
                & (col(Evidence.org_id) == ChangeItemEvidence.org_id),
            )
            .where(
                ChangeItemEvidence.org_id == item.org_id,
                ChangeItemEvidence.item_id == item.id,
            )
        ).all()
    }


def effective_evidence(
    session: Session,
    item: ChangeItem,
    target: ProfileClaim | None,
    evidence: dict[UUID, tuple[Evidence, EvidenceRelation]],
) -> dict[UUID, tuple[Evidence, EvidenceRelation]]:
    if item.op == ChangeOp.RETRACT:
        return {}
    result = evidence.copy()
    if item.op == ChangeOp.ATTACH_EVIDENCE and target is not None:
        for link, e in session.exec(
            select(ClaimEvidence, Evidence)
            .join(
                Evidence,
                (col(Evidence.id) == ClaimEvidence.evidence_id)
                & (col(Evidence.org_id) == ClaimEvidence.org_id),
            )
            .where(
                ClaimEvidence.org_id == item.org_id,
                ClaimEvidence.claim_id == target.id,
            )
        ).all():
            result.setdefault(e.id, (e, link.relation))
    return result


def jira_only(evidence: dict[UUID, tuple[Evidence, EvidenceRelation]]) -> bool:
    supports = [
        e for e, relation in evidence.values() if relation == EvidenceRelation.SUPPORTS
    ]
    return bool(supports) and all(e.source == "jira" for e in supports)


def publish(
    session: Session,
    request: ProfileChangeRequest,
    items: list[ChangeItem],
    decision: ReviewDecision,
) -> ProfileVersion:
    org_id, subject = request.org_id, request.subject_employee_id
    schema = get_schema(session, org_id)
    definitions = {c.key: c for c in list_capabilities(session, org_id, schema.id)}
    current = current_claims(session, org_id, subject)
    publisher: dict[str, JsonValue] = {
        "actor_type": "user" if decision.reviewer_user_id else "system_worker",
        "user_id": str(decision.reviewer_user_id)
        if decision.reviewer_user_id
        else None,
        "via": decision.via.value,
        "request_id": str(request.id),
        "decision_id": str(decision.id),
    }
    for item in items:
        original = session.exec(
            select(CapabilityDefinition).where(
                CapabilityDefinition.org_id == org_id,
                CapabilityDefinition.id == item.capability_definition_id,
            )
        ).one()
        target = current.get(original.key)
        if (target.id if target else None) != item.target_claim_id:
            raise RequestConflict("目标判断已被其他请求取代，请重新查看")
        evidence = effective_evidence(
            session, item, target, item_evidence(session, item)
        )
        if item.op in (ChangeOp.ATTACH_EVIDENCE, ChangeOp.RETRACT) and target is None:
            raise RequestConflict("目标判断不存在，请重新查看")
        if item.op == ChangeOp.ATTACH_EVIDENCE and target is not None:
            after = ClaimAfter.model_validate(
                target.model_dump(
                    include={"statement", "level", "assessment_status", "confidence"}
                )
            )
        elif item.op == ChangeOp.RETRACT and target is not None:
            after = ClaimAfter(
                statement=target.statement, assessment_status=AssessmentStatus.UNKNOWN
            )
        else:
            after = ClaimAfter.model_validate(item.after)
            after.assessment_status = AssessmentStatus.CONFIRMED
        if jira_only(evidence) or (
            decision.via == ReviewVia.POLICY and item.op != ChangeOp.RETRACT
        ):
            after.assessment_status = AssessmentStatus.TENTATIVE
        claim = ProfileClaim(
            org_id=org_id,
            subject_employee_id=subject,
            capability_definition_id=definitions[original.key].id,
            **after.model_dump(
                include={"statement", "level", "assessment_status", "confidence"}
            ),
            explanation=item.explanation,
            generated_by=publisher,
            supersedes_claim_id=item.target_claim_id,
        )
        session.add(claim)
        session.flush()
        session.add_all(
            [
                ClaimEvidence(
                    org_id=org_id, claim_id=claim.id, evidence_id=id_, relation=relation
                )
                for id_, (_, relation) in evidence.items()
            ]
        )
        item.published_claim_id = claim.id
        current[original.key] = claim
    session.flush()
    previous = session.exec(
        select(ProfileVersion)
        .where(
            ProfileVersion.org_id == org_id,
            ProfileVersion.subject_employee_id == subject,
        )
        .order_by(col(ProfileVersion.revision).desc())
    ).first()
    version = ProfileVersion(
        org_id=org_id,
        subject_employee_id=subject,
        revision=previous.revision + 1 if previous else 1,
        schema_version_id=schema.id,
        published_at=utc_now(),
        published_by=publisher,
        supersedes_version_id=previous.id if previous else None,
        snapshot=build_profile_snapshot(
            session,
            org_id=org_id,
            employee_id=subject,
            claim_ids=[c.id for c in current.values()],
        ),
    )
    session.add(version)
    session.flush()
    return version
