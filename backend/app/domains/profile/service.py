from uuid import UUID

from fastapi import HTTPException
from pydantic import JsonValue
from sqlmodel import Session, col, func, select

from app.authz.principal import Principal
from app.domains.base import utc_now
from app.domains.capability.department import department_keys
from app.domains.capability.models import CapabilityDefinition, CapabilitySchemaVersion
from app.domains.capability.schemas import CapabilityPublic, SchemaVersionPublic
from app.domains.capability.service import (
    SchemaNotFound,
    get_schema,
    level_exists,
    list_capabilities,
)
from app.domains.drafting.update_settings import months_before, read_update_settings
from app.domains.evidence.models import Evidence, EvidenceStatus
from app.domains.evidence.service import evidence_access, restricted_title
from app.domains.org.models import Employee, OrgMembership
from app.domains.org.service import employee_summary, visible_employee
from app.domains.profile.models import (
    AssessmentStatus,
    ClaimEvidence,
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
        super().__init__(404, "画像版本不存在")


def profile_items(
    session: Session,
    principal: Principal,
    schema_id: UUID,
    claims: list[SnapshotClaim],
    disputed_keys: set[str] | None = None,
) -> tuple[list[ProfileItem], ProfileCounts]:
    capabilities = list_capabilities(session, principal.org_id, schema_id)
    access = evidence_access(
        session,
        principal,
        {ref.evidence_id for claim in claims for ref in claim.evidence},
    )
    for snapshot in claims:
        for ref in snapshot.evidence:
            record = access.get(ref.evidence_id)
            ref.redacted = record is None or not record[1]
            ref.occurred_at = record[0].occurred_at if record else None
            if ref.redacted:
                ref.title = restricted_title(ref.kind)
    originals = {c.id: c for c in capabilities}
    missing_ids = {c.capability_definition_id for c in claims} - originals.keys()
    if missing_ids:
        originals.update(
            (c.id, c)
            for c in session.exec(
                select(CapabilityDefinition).where(
                    CapabilityDefinition.org_id == principal.org_id,
                    col(CapabilityDefinition.id).in_(missing_ids),
                )
            ).all()
        )
    by_key = {
        originals[c.capability_definition_id].key: c
        for c in claims
        if c.capability_definition_id in originals
    }
    active_keys = {c.key for c in capabilities}
    removed = [
        originals[c.capability_definition_id]
        for k, c in by_key.items()
        if k not in active_keys
    ]
    months = read_update_settings(session, principal.org_id).stale_after_months
    cutoff = months_before(utc_now(), months)
    items = []
    for capability in [*capabilities, *removed]:
        claim = by_key.get(capability.key)
        adjusted = bool(
            claim and not level_exists(capability.level_rubric, claim.level)
        )
        old_description = None
        if claim and adjusted:
            rubric = originals[claim.capability_definition_id].level_rubric or {}
            levels = rubric.get("levels", [])
            if isinstance(levels, list):
                old_description = next(
                    (
                        str(v.get("description") or v.get("label") or claim.level)
                        for v in levels
                        if isinstance(v, dict) and v.get("key") == claim.level
                    ),
                    claim.level,
                )
        dates = (
            [
                access[ref.evidence_id][0].occurred_at
                for ref in claim.evidence
                if ref.evidence_id in access
                and access[ref.evidence_id][0].status == EvidenceStatus.ACTIVE
                and access[ref.evidence_id][0].occurred_at is not None
            ]
            if claim
            else []
        )
        latest = max((date for date in dates if date is not None), default=None)
        items.append(
            ProfileItem(
                capability=CapabilityPublic.model_validate(capability),
                claim=claim,
                status=AssessmentStatus.DISPUTED
                if disputed_keys and capability.key in disputed_keys
                else claim.assessment_status
                if claim
                else AssessmentStatus.UNKNOWN,
                evidence_stale=bool(
                    claim
                    and claim.assessment_status == AssessmentStatus.CONFIRMED
                    and latest
                    and latest < cutoff
                ),
                retired=capability.key not in active_keys,
                level_adjusted=adjusted,
                original_level_description=old_description,
            )
        )
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
        session, visible_employee(session, principal, employee_id), principal
    )
    own, inherited = department_keys(
        session,
        principal.org_id,
        employee.department.id if employee.department else None,
    )
    keys = own | inherited
    try:
        schema = get_schema(session, principal.org_id)
    except SchemaNotFound:
        return ProfileView(
            employee=employee,
            schema_version=None,
            latest_published=employee.latest_published,
            items=[],
            counts=ProfileCounts(),
            department_capabilities_configured=bool(keys),
        )
    from app.domains.change_request.models import ChangeItem
    from app.domains.review.models import AppealStatus, ReviewAppeal

    disputes = (
        select(func.array_agg(CapabilityDefinition.key))
        .select_from(ReviewAppeal)
        .join(
            ChangeItem,
            (col(ChangeItem.id) == ReviewAppeal.item_id)
            & (col(ChangeItem.org_id) == ReviewAppeal.org_id),
        )
        .join(
            CapabilityDefinition,
            (col(CapabilityDefinition.id) == ChangeItem.capability_definition_id)
            & (col(CapabilityDefinition.org_id) == ChangeItem.org_id),
        )
        .where(
            ReviewAppeal.org_id == principal.org_id,
            ReviewAppeal.employee_id == employee_id,
            ReviewAppeal.status == AppealStatus.OPEN,
        )
        .scalar_subquery()
    )
    # Anchor on the employee so appeals also display before any claim is published.
    records = session.exec(
        select(ProfileClaim, disputes)
        .select_from(Employee)
        .outerjoin(
            ProfileClaim,
            (col(ProfileClaim.org_id) == Employee.org_id)
            & (col(ProfileClaim.subject_employee_id) == Employee.id),
        )
        .where(Employee.org_id == principal.org_id, Employee.id == employee_id)
        .order_by(col(ProfileClaim.created_at), col(ProfileClaim.id))
    ).all()
    rows = [claim for claim, _ in records if claim is not None]
    disputed_keys = set(records[0][1] or []) if records else set()
    superseded = {c.supersedes_claim_id for c in rows}
    current = {
        c.id: SnapshotClaim.model_validate(c) for c in rows if c.id not in superseded
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
    items, counts = profile_items(
        session, principal, schema.id, list(current.values()), disputed_keys
    )
    for item in items:
        item.in_department = not item.retired and item.capability.key in keys
    return ProfileView(
        employee=employee,
        schema_version=SchemaVersionPublic.model_validate(schema),
        latest_published=employee.latest_published,
        items=items,
        counts=counts,
        department_capabilities_configured=bool(keys),
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
        user.id: employee_name or user.full_name or user.username
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
            via=str(v.published_by["via"]) if v.published_by.get("via") else None,
            request_id=UUID(str(v.published_by["request_id"]))
            if v.published_by.get("request_id")
            else None,
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
