from uuid import UUID

from pydantic import JsonValue
from sqlmodel import Session, col, select

from app.domains.evidence.models import Evidence
from app.domains.profile.models import ClaimEvidence, ProfileClaim


class SnapshotError(ValueError):
    """A requested claim does not belong to this employee and tenant."""


def build_profile_snapshot(
    session: Session, *, org_id: UUID, employee_id: UUID, claim_ids: list[UUID]
) -> dict[str, JsonValue]:
    claims: list[JsonValue] = []
    for claim_id in claim_ids:
        claim = session.exec(
            select(ProfileClaim).where(
                ProfileClaim.org_id == org_id,
                ProfileClaim.subject_employee_id == employee_id,
                ProfileClaim.id == claim_id,
            )
        ).one_or_none()
        if claim is None:
            raise SnapshotError(f"Claim {claim_id} is outside the requested employee")
        row = claim.model_dump(
            mode="json",
            include={
                "id",
                "capability_definition_id",
                "statement",
                "level",
                "assessment_status",
                "review_status",
                "confidence",
                "explanation",
                "generated_by",
                "supersedes_claim_id",
            },
        )
        row["evidence"] = [
            {
                "id": str(evidence.id),
                "kind": evidence.kind.value,
                "title": evidence.title,
                "relation": link.relation.value,
            }
            for link, evidence in session.exec(
                select(ClaimEvidence, Evidence)
                .join(
                    Evidence,
                    (col(Evidence.id) == ClaimEvidence.evidence_id)
                    & (col(Evidence.org_id) == ClaimEvidence.org_id),
                )
                .where(
                    ClaimEvidence.org_id == org_id, ClaimEvidence.claim_id == claim_id
                )
                .order_by(col(Evidence.id))
            )
        ]
        claims.append(row)
    return {"claims": claims}
