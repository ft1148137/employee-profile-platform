from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ModelWrapValidatorHandler,
    ValidationError,
    model_validator,
)
from pydantic_core import ErrorDetails, InitErrorDetails, PydanticCustomError
from sqlmodel import SQLModel

from app.domains.capability.schemas import CapabilityPublic
from app.domains.change_request.claim import (
    EXPLANATION_MAX,
    ITEMS_MAX,
    STATEMENT_MAX,
    limit_message,
)
from app.domains.change_request.claim import ClaimAfter as ClaimAfter
from app.domains.change_request.models import (
    ChangeOp,
    ItemDecision,
    RequestOrigin,
    RequestStatus,
    SubmissionVia,
)
from app.domains.profile.schemas import ClaimEvidencePublic, ClaimPublic


class RequestGeneratedBy(SQLModel):
    mode: str | None = None
    model: str | None = None


class ChangeItemPublic(SQLModel):
    id: UUID
    op: ChangeOp
    capability: CapabilityPublic
    target_claim_id: UUID | None
    after: ClaimAfter | None
    explanation: str | None
    uncertainty: str | None
    owner_statement: str | None = None
    objection: str | None = None
    appeal_id: UUID | None = None
    decision: ItemDecision | None = None
    decision_reason: str | None = None
    published_claim_id: UUID | None = None
    evidence: list[ClaimEvidencePublic] = Field(default_factory=list)


class RequestReviewer(SQLModel):
    name: str
    role: Literal["直属上级", "部门经理", "代理审批", "企业管理员"]


class ChangeRequestPublic(SQLModel):
    id: UUID
    origin: RequestOrigin
    status: RequestStatus
    created_at: datetime
    updated_at: datetime
    generated_by: RequestGeneratedBy
    revision: int
    submitted_via: SubmissionVia | None
    client_name: str | None
    submitted_at: datetime | None
    decided_at: datetime | None = None
    parent_request_id: UUID | None = None
    items: list[ChangeItemPublic] = Field(default_factory=list)
    reviewers: list[RequestReviewer] = Field(default_factory=list)


class ChangeRequestsPublic(SQLModel):
    data: list[ChangeRequestPublic]
    count: int


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProfileInput(ToolInput):
    @model_validator(mode="wrap")
    @classmethod
    def chinese_limits(cls, value: Any, handler: ModelWrapValidatorHandler[Any]) -> Any:
        try:
            return handler(value)
        except ValidationError as exc:
            errors: list[InitErrorDetails] = []
            for e in exc.errors():
                message = limit_validation_message(e)
                errors.append(
                    InitErrorDetails(
                        type=PydanticCustomError(
                            "profile_input_limit",
                            "{message}",
                            {
                                "message": message,
                                "limit_type": e.get("ctx", {}).get(
                                    "limit_type", e["type"]
                                ),
                            },
                        )
                        if message
                        else e["type"],
                        loc=e["loc"],
                        input=e.get("input"),
                        ctx=e.get("ctx", {}),
                    )
                )
            raise ValidationError.from_exception_data(cls.__name__, errors) from None


def limit_validation_message(error: ErrorDetails) -> str | None:
    location = error["loc"]
    field = str(location[-1]) if location else ""
    value = error.get("input")
    code = error.get("ctx", {}).get("limit_type", error["type"])
    if (code == "too_long" and field == "items" and isinstance(value, list)) or (
        code == "string_too_long"
        and field
        in {
            "statement",
            "explanation",
            "basis",
            "uncertainty",
            "owner_statement",
            "objection",
        }
        and isinstance(value, str)
    ):
        index = next(
            (
                location[i + 1]
                for i, part in enumerate(location[:-1])
                if part == "items" and isinstance(location[i + 1], int)
            ),
            None,
        )
        return f"{'.'.join(map(str, location))}：" + limit_message(
            field, len(value), index if isinstance(index, int) else None
        )
    return None


def validation_message(
    exc: ValidationError, fallback: str = "工具参数无效，请核对字段和格式"
) -> str:
    return (
        "；".join(filter(None, (limit_validation_message(e) for e in exc.errors())))
        or fallback
    )


class ItemInput(ProfileInput):
    op: ChangeOp
    capability_key: str
    # Compare against stored text in the service before enforcing these schema limits.
    statement: str = Field(min_length=1, json_schema_extra={"maxLength": STATEMENT_MAX})
    level: str | None = None
    owner_statement: str | None = Field(
        default=None, json_schema_extra={"maxLength": EXPLANATION_MAX}
    )
    objection: str | None = Field(
        default=None, json_schema_extra={"maxLength": EXPLANATION_MAX}
    )
    evidence_ids: list[UUID] = Field(default_factory=list)
    uncertainty: str | None = Field(
        default=None, json_schema_extra={"maxLength": EXPLANATION_MAX}
    )


class DraftInput(ProfileInput):
    items: list[ItemInput] = Field(max_length=ITEMS_MAX)


class SubmitInput(ToolInput):
    revision: int = Field(ge=1)


class PreviewItem(SQLModel):
    before: ClaimPublic | None
    after: ChangeItemPublic
    warning: str | None = None
    current_changed_at: datetime | None = None


class RequestPreview(SQLModel):
    request_id: UUID
    revision: int
    items: list[PreviewItem]
    reviewers: list[RequestReviewer] = Field(default_factory=list)
