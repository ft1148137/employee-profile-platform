from datetime import datetime
from uuid import UUID

from pydantic import AliasChoices, Field
from sqlmodel import SQLModel

from app.domains.capability.schemas import CapabilityPublic, SchemaVersionPublic
from app.domains.evidence.models import EvidenceKind
from app.domains.org.schemas import EmployeeSummary, PublishedSummary
from app.domains.profile.models import (
    AssessmentStatus,
    ClaimReviewStatus,
    Confidence,
    EvidenceRelation,
)


class GeneratedBy(SQLModel):
    actor_type: str
    model: str | None = None
    model_version: str | None = None


class ClaimEvidencePublic(SQLModel):
    evidence_id: UUID = Field(validation_alias=AliasChoices("evidence_id", "id"))
    kind: EvidenceKind
    title: str
    occurred_at: datetime | None = None
    relation: EvidenceRelation
    redacted: bool = True


class ClaimPublic(SQLModel):
    id: UUID
    statement: str
    level: str | None
    assessment_status: AssessmentStatus
    review_status: ClaimReviewStatus
    confidence: Confidence | None
    explanation: str | None
    generated_by: GeneratedBy
    created_at: datetime | None = None
    evidence: list[ClaimEvidencePublic] = Field(default_factory=list)


class ProfileItem(SQLModel):
    capability: CapabilityPublic
    status: AssessmentStatus
    claim: ClaimPublic | None


class ProfileCounts(SQLModel):
    confirmed: int = 0
    tentative: int = 0
    unknown: int = 0
    disputed: int = 0


class ProfileView(SQLModel):
    employee: EmployeeSummary
    schema_version: SchemaVersionPublic
    latest_published: PublishedSummary | None
    counts: ProfileCounts
    items: list[ProfileItem]


class PublisherPublic(SQLModel):
    actor_type: str
    display_name: str | None


class ProfileVersionSummary(PublishedSummary):
    published_by: PublisherPublic
    schema_version: SchemaVersionPublic


class ProfileVersionsPublic(SQLModel):
    data: list[ProfileVersionSummary]
    count: int


class ProfileVersionView(ProfileVersionSummary):
    counts: ProfileCounts
    items: list[ProfileItem]


class SnapshotClaim(ClaimPublic):
    capability_definition_id: UUID
