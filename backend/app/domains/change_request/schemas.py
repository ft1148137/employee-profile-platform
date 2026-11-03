from pydantic import BaseModel, ConfigDict

from app.domains.profile.models import AssessmentStatus, Confidence


class ClaimAfter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    statement: str
    level: str | None = None
    assessment_status: AssessmentStatus
    confidence: Confidence | None = None
