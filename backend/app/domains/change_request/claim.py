from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.domains.profile.models import AssessmentStatus, Confidence


class ClaimAfter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    statement: str
    level: str | None = None
    assessment_status: AssessmentStatus
    confidence: Confidence | None = None
    evidence_count: int | None = Field(default=None, ge=0)
    source_count: int | None = Field(default=None, ge=0)
    latest_evidence_at: AwareDatetime | None = None
