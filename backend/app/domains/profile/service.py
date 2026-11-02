from uuid import UUID

from fastapi import HTTPException
from pydantic import JsonValue
from sqlmodel import Session, col, select

from app.authz.principal import Principal
from app.domains.capability.models import CapabilitySchemaVersion
from app.domains.capability.schemas import CapabilityPublic, SchemaVersionPublic
from app.domains.capability.service import get_schema, list_capabilities
from app.domains.evidence.models import Evidence
from app.domains.evidence.service import evidence_access, restricted_title
from app.domains.org.models import Employee, OrgMembership
from app.domains.org.service import employee_summary, visible_employee
from app.domains.profile.models import (
    AssessmentStatus,
    ClaimEvidence,
    ClaimReviewStatus,
    ProfileClaim,
    ProfileVersion,
)
from app.domains.profile.schemas import (
    ClaimEvidencePublic,
    ProfileCounts,
    ProfileItem,
    ProfileVersionsPublic,
    ProfileVersionSummary,
    ProfileVersionView,
    ProfileView,
    PublisherPublic,
    SnapshotClaim,
)
from app.models import User


class VersionNotFound(HTTPException):
    def __init__(self) -> None:
        super().__init__(404, "Profile version not found")


def profile_items(
    session: Session, principal: Principal, schema_id: UUID, claims: list[SnapshotClaim]
) -> tuple[list[ProfileItem], ProfileCounts]:
    capabilities = list_capabilities(session, principal.org_id, schema_id)
    access = evidence_access(
        session,
        principal,
        {ref.evidence_id for claim in claims for ref in claim.evidence},
    )
    for claim in claims:
        for ref in claim.evidence:
            record = access.get(ref.evidence_id)
            ref.redacted = record is None or not record[1]
            ref.occurred_at = record[0].occurred_at if record else None
            if ref.redacted:
                ref.title = restricted_title(ref.kind)
    by_capability = {claim.capability_definition_id: claim for claim in claims}
    items = [
        ProfileItem(
            capability=CapabilityPublic.model_validate(capability),
            claim=by_capability.get(capability.id),
            status=by_capability[capability.id].assessment_status
            if capability.id in by_capability
            else AssessmentStatus.UNKNOWN,
        )
        for capability in capabilities
    ]
    return items, ProfileCounts(
        **{
            status.value: sum(item.status == status for item in items)
            for status in AssessmentStatus
        }
    )


def read_profile(
    session: Session, principal: Principal, employee_id: UUID
) -> ProfileView:
    employee = employee_summary(
        session, visible_employee(session, principal, employee_id)
    )
    schema = get_schema(session, principal.org_id)
    rows = session.exec(
        select(ProfileClaim)
        .where(
            ProfileClaim.org_id == principal.org_id,
            ProfileClaim.subject_employee_id == employee_id,
        )
        .order_by(col(ProfileClaim.created_at), col(ProfileClaim.id))
    ).all()
    superseded = {(c.capability_definition_id, c.supersedes_claim_id) for c in rows}
    current = {
        c.capability_definition_id: SnapshotClaim.model_validate(c)
        for c in rows
        if (c.capability_definition_id, c.id) not in superseded
        and c.review_status != ClaimReviewStatus.REJECTED
    }
    by_id = {claim.id: claim for claim in current.values()}
    for link, evidence in session.exec(
        select(ClaimEvidence, Evidence)
        .join(
            Evidence,
            (col(Evidence.id) == ClaimEvidence.evidence_id)
            & (col(Evidence.org_id) == ClaimEvidence.org_id),
        )
        .where(
            ClaimEvidence.org_id == principal.org_id,
            col(ClaimEvidence.claim_id).in_(by_id),
        )
        .order_by(col(Evidence.id))
    ).all():
        by_id[link.claim_id].evidence.append(
            ClaimEvidencePublic(
                evidence_id=evidence.id,
                kind=evidence.kind,
                title=evidence.title,
                relation=link.relation,
            )
        )
    items, counts = profile_items(session, principal, schema.id, list(current.values()))
    return ProfileView(
        employee=employee,
        schema_version=SchemaVersionPublic.model_validate(schema),
        latest_published=employee.latest_published,
        items=items,
        counts=counts,
    )


def version_summaries(
    session: Session, principal: Principal, versions: list[ProfileVersion]
) -> list[ProfileVersionSummary]:
    schemas = {
        s.id: SchemaVersionPublic.model_validate(s)
        for s in session.exec(
            select(CapabilitySchemaVersion).where(
                CapabilitySchemaVersion.org_id == principal.org_id,
                col(CapabilitySchemaVersion.id).in_(
                    {v.schema_version_id for v in versions}
                ),
            )
        ).all()
    }
    user_ids = {
        UUID(str(v.published_by["user_id"]))
        for v in versions
        if v.published_by.get("actor_type") == "user" and v.published_by.get("user_id")
    }
    names = {
        user.id: employee_name or user.full_name
        for user, employee_name in session.exec(
            select(User, Employee.display_name)
            .outerjoin(
                OrgMembership,
                (col(OrgMembership.user_id) == User.id)
                & (col(OrgMembership.org_id) == principal.org_id),
            )
            .outerjoin(
                Employee,
                (col(Employee.id) == OrgMembership.employee_id)
                & (col(Employee.org_id) == principal.org_id),
            )
            .where(col(User.id).in_(user_ids))
        ).all()
    }
    return [
        ProfileVersionSummary(
            revision=v.revision,
            published_at=v.published_at,
            schema_version=schemas[v.schema_version_id],
            published_by=PublisherPublic(
                actor_type=str(v.published_by["actor_type"]),
                display_name=names.get(UUID(str(v.published_by["user_id"])))
                if v.published_by.get("user_id")
                else None,
            ),
        )
        for v in versions
    ]


def list_versions(
    session: Session, principal: Principal, employee_id: UUID
) -> ProfileVersionsPublic:
    visible_employee(session, principal, employee_id)
    versions = list(
        session.exec(
            select(ProfileVersion)
            .where(
                ProfileVersion.org_id == principal.org_id,
                ProfileVersion.subject_employee_id == employee_id,
            )
            .order_by(col(ProfileVersion.revision).desc())
        ).all()
    )
    return ProfileVersionsPublic(
        data=version_summaries(session, principal, versions), count=len(versions)
    )


def read_version(
    session: Session, principal: Principal, employee_id: UUID, revision: int
) -> ProfileVersionView:
    visible_employee(session, principal, employee_id)
    version = session.exec(
        select(ProfileVersion).where(
            ProfileVersion.org_id == principal.org_id,
            ProfileVersion.subject_employee_id == employee_id,
            ProfileVersion.revision == revision,
        )
    ).first()
    if version is None:
        raise VersionNotFound()
    raw_claims = version.snapshot.get("claims", [])
    claims = (
        [SnapshotClaim.model_validate(c) for c in raw_claims]
        if isinstance(raw_claims, list)
        else []
    )
    items, counts = profile_items(session, principal, version.schema_version_id, claims)
    return ProfileVersionView(
        **version_summaries(session, principal, [version])[0].model_dump(),
        items=items,
        counts=counts,
    )


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
