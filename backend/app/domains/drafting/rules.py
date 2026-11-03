import hashlib
import re
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from pydantic import Field

from app.core.config import settings
from app.domains.capability.models import CapabilityDefinition
from app.domains.capability.templates import InvalidTemplate, get_template, read_yaml
from app.domains.change_request.claim import ClaimAfter
from app.domains.drafting.collect import CollectedRecord
from app.domains.profile.models import AssessmentStatus, EvidenceRelation


def load_keywords(template_ref: str | None) -> tuple[dict[str, list[str]], str | None]:
    template_id = (template_ref or "").partition("@")[0].removesuffix(".zh-CN")
    path = settings.CAPABILITY_TEMPLATES_DIR / "keywords" / f"{template_id}.yaml"
    if not template_id or path.name != f"{template_id}.yaml" or not path.is_file():
        return {}, None
    try:
        keywords, content = read_yaml(
            path,
            dict[
                str,
                Annotated[
                    list[Annotated[str, Field(min_length=1)]],
                    Field(min_length=3, max_length=10),
                ],
            ],
        )
    except InvalidTemplate as error:
        raise InvalidTemplate(
            f"关键词文件 {path.name} 格式无效，每项需有 3–10 个非空关键词"
        ) from error
    if keywords.keys() != {c.key for c in get_template(template_id).capabilities}:
        raise InvalidTemplate(f"{path.name}: 能力 key 与模板不一致")
    return keywords, f"{template_id}@{hashlib.sha256(content).hexdigest()[:12]}"


@dataclass
class DraftItem:
    capability_id: UUID
    after: ClaimAfter
    explanation: str
    uncertainty: str
    evidence: dict[UUID, EvidenceRelation]


def relation(record: CollectedRecord) -> EvidenceRelation:
    roles = record.evidence.content.get("roles")
    return (
        EvidenceRelation.CONTEXT
        if record.evidence.source == "jira" and roles == ["reporter"]
        else EvidenceRelation.SUPPORTS
    )


def draft(
    records: list[CollectedRecord],
    capabilities: list[CapabilityDefinition],
    keywords: dict[str, list[str]],
) -> list[DraftItem]:
    items = []
    for capability in capabilities:
        matches: list[CollectedRecord] = []
        hits: set[str] = set()
        for record in records:
            task = record.task
            attributes = task.attributes if task else record.evidence.content
            text = " ".join(
                str(value or "")
                for value in (
                    task.title if task else record.evidence.title,
                    task.task_type if task else attributes.get("type"),
                    attributes.get("labels"),
                    attributes.get("components"),
                    attributes.get("project"),
                )
            ).casefold()
            found = {
                word
                for word in keywords.get(capability.key, [])
                if (
                    re.search(
                        r"(?<![a-z0-9])" + re.escape(word.casefold()) + r"(?![a-z0-9])",
                        text,
                    )
                    if word.isascii()
                    else word.casefold() in text
                )
            }
            if found:
                hits.update(found)
                matches.append(record)
        supports = [r for r in matches if relation(r) == EvidenceRelation.SUPPORTS]
        if not supports:
            continue
        count = len({r.experience_id for r in supports})
        context = sum(relation(r) == EvidenceRelation.CONTEXT for r in matches)
        items.append(
            DraftItem(
                capability.id,
                ClaimAfter(
                    statement=f"有 {count} 项与「{capability.name}」相关的工作记录",
                    assessment_status=AssessmentStatus.TENTATIVE,
                ),
                "命中的关键词："
                + "、".join(sorted(hits))
                + "；记录："
                + "；".join(
                    f"{r.evidence.source_ref} {r.evidence.title}" for r in matches[:5]
                ),
                "只根据工作记录推断，指派人或署名人不一定是实际完成人；没有评估等级。"
                + (f"其中 {context} 项记录里只是报告人。" if context else ""),
                {r.evidence.id: relation(r) for r in matches},
            )
        )
    return items
