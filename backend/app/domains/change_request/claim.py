from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.domains.profile.models import AssessmentStatus, Confidence

STATEMENT_MAX = 200
EXPLANATION_MAX = 4000
ITEMS_MAX = 50


def limit_message(field: str, actual: int, index: int | None = None) -> str:
    if field == "items":
        return f"每次最多 {ITEMS_MAX} 条改动，本次有 {actual} 条"
    label = "能力说明" if field == "statement" else "说明"
    limit = STATEMENT_MAX if field == "statement" else EXPLANATION_MAX
    position = f"第 {index + 1} 条" if index is not None else "本次"
    return f"{label}最多 {limit} 字，{position}有 {actual} 字"


def text_limit(
    field: str, value: str | None, index: int, previous: str | None = None
) -> str | None:
    limit = STATEMENT_MAX if field == "statement" else EXPLANATION_MAX
    if value is not None and value != previous and len(value) > limit:
        return limit_message(field, len(value), index)
    return None


class ClaimAfter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    statement: str
    level: str | None = None
    assessment_status: AssessmentStatus
    confidence: Confidence | None = None
    evidence_count: int | None = Field(default=None, ge=0)
    source_count: int | None = Field(default=None, ge=0)
    latest_evidence_at: AwareDatetime | None = None
