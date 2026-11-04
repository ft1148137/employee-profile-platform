from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
    model_validator,
)

from app.domains.capability.models import CapabilityDefinition
from app.domains.change_request.claim import ClaimAfter
from app.domains.drafting.collect import CollectedRecord, relation
from app.domains.evidence.models import Visibility
from app.domains.model.client import (
    ModelOutputInvalid,
    ModelUnavailable,
)
from app.domains.model.pi_client import PiModelConfig, ToolDefinition, run_turn
from app.domains.profile.models import AssessmentStatus, EvidenceRelation


@dataclass
class DraftItem:
    capability_id: UUID
    after: ClaimAfter
    explanation: str
    uncertainty: str
    evidence: dict[UUID, EvidenceRelation]


PROMPT_VERSION = "initial-draft-v1"
SYSTEM_PROMPT = """只根据给出的记录起草能力陈述，没有记录支持的能力不要输出，未知不等于不会。
指派人或报告人不一定是实际完成人，要在 uncertainty 中说明。同一经验组只算一项经验。
等级只能用该能力给出的等级 key，没有把握时为 null。不做绩效、薪酬、晋升判断。
explanation 不要写证据条数、来源数和日期，系统会按实际证据补在开头。
只返回规定格式的 JSON，evidence 使用本次记录的 ref。"""


class LevelInput(BaseModel):
    key: str
    label: str
    description: str | None = None


class RubricInput(BaseModel):
    levels: list[LevelInput] = Field(default_factory=list)


class CapabilityInput(BaseModel):
    key: str
    name: str
    category: str | None
    description: str | None
    levels: list[LevelInput]


class RecordInput(BaseModel):
    ref: str
    source: str
    type: str | None
    title: str
    status: str | None
    labels: list[str]
    components: list[str]
    project: str | None
    date: str | None
    roles: list[str]
    detail: str = Field(max_length=1000)
    experience: int


class DraftInput(BaseModel):
    capabilities: list[CapabilityInput]
    records: list[RecordInput]


class ModelItem(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    capability_key: str
    statement: str = Field(min_length=1, max_length=200)
    level: str | None
    evidence: list[str] = Field(min_length=1)
    explanation: str = Field(min_length=1, max_length=500)
    uncertainty: str = Field(min_length=1, max_length=300)


def draft(
    records: list[CollectedRecord],
    capabilities: list[CapabilityDefinition],
    config: PiModelConfig,
) -> tuple[list[DraftItem], dict[str, JsonValue]]:
    # shortcut: Send only the latest 80 records; batch by token budget when larger histories are needed.
    records = sorted(
        (r for r in records if r.evidence.visibility == Visibility.STANDARD),
        key=lambda r: r.evidence.occurred_at or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )[:80]
    refs = {f"R{i}": r for i, r in enumerate(records, 1)}
    groups = {
        key: i
        for i, key in enumerate(dict.fromkeys(r.experience_id for r in records), 1)
    }
    body = DraftInput(
        capabilities=[
            CapabilityInput(
                key=c.key,
                name=c.name,
                category=c.category,
                description=c.description,
                levels=RubricInput.model_validate(c.level_rubric or {}).levels,
            )
            for c in capabilities
        ],
        records=[
            RecordInput.model_validate(
                {
                    "ref": ref,
                    "source": "Jira"
                    if r.evidence.source == "jira"
                    else r.evidence.source.removeprefix("csv:"),
                    "type": r.evidence.content.get("type"),
                    "title": r.evidence.title,
                    "status": r.evidence.content.get("status"),
                    "labels": r.evidence.content.get("labels") or [],
                    "components": r.evidence.content.get("components") or [],
                    "project": r.evidence.content.get("project"),
                    "date": r.evidence.occurred_at.date().isoformat()
                    if r.evidence.occurred_at
                    else None,
                    "roles": r.evidence.content.get("roles") or [],
                    "detail": str(r.evidence.content.get("detail") or "")[:1000],
                    "experience": groups[r.experience_id],
                }
            )
            for ref, r in refs.items()
        ],
    )
    definitions = {c.key: c for c in capabilities}
    levels = {c.key: {level.key for level in c.levels} for c in body.capabilities}

    class DraftOutput(BaseModel):
        model_config = ConfigDict(extra="forbid")
        items: list[ModelItem]

        @model_validator(mode="after")
        def validate_references(self) -> Self:
            seen: set[str] = set()
            for item in self.items:
                if (
                    item.capability_key not in definitions
                    or item.capability_key in seen
                ):
                    raise ValueError("能力不在给定词典中或重复")
                seen.add(item.capability_key)
                if not set(item.evidence) <= refs.keys():
                    raise ValueError("证据 ref 不在本次记录中")
                if (
                    item.level is not None
                    and item.level not in levels[item.capability_key]
                ):
                    raise ValueError("等级不在该能力的给定等级中")
            return self

    output = None
    failures = 0

    def submit(_name: str, arguments: dict[str, JsonValue]) -> JsonValue:
        nonlocal output, failures
        if output is not None:
            return {"accepted": True, "message": "已提交"}
        try:
            output = DraftOutput.model_validate(arguments)
        except ValidationError:
            failures += 1
            if failures >= 3:
                raise ModelOutputInvalid() from None
            return {
                "accepted": False,
                "error": "提交不符合要求，请只用给定的能力、等级和 R 编号重新提交",
            }
        return {"accepted": True}

    while output is None and failures < 3:
        try:
            run_turn(
                config,
                system_prompt=SYSTEM_PROMPT + "使用 submit_draft 提交结果。",
                input=body.model_dump_json(),
                tools=[
                    ToolDefinition(
                        name="submit_draft",
                        description="提交初始画像候选",
                        parameters=DraftOutput.model_json_schema(),
                    )
                ],
                on_tool_call=submit,
            )
        except ModelUnavailable:
            if failures >= 3:
                raise ModelOutputInvalid() from None
            raise
        if output is None:
            failures += 1
    if output is None:
        raise ModelOutputInvalid()
    provider, model_id, effort = config.provider, config.id, config.thinkingLevel
    assert output is not None
    return [
        DraftItem(
            definitions[item.capability_key].id,
            ClaimAfter(
                statement=item.statement,
                level=item.level,
                assessment_status=AssessmentStatus.TENTATIVE,
            ),
            item.explanation,
            item.uncertainty,
            {refs[ref].evidence.id: relation(refs[ref]) for ref in item.evidence},
        )
        for item in output.items
    ], {
        "mode": "model",
        "provider": provider,
        "model": model_id,
        "prompt_version": PROMPT_VERSION,
        "reasoning_effort": effort,
    }
