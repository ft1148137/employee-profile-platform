"""Material proposals use the existing dictionary editor and publication rules."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, RootModel
from sqlmodel import Session

from app.authz.principal import Principal
from app.domains.account.service import require_admin
from app.domains.admin_assistant.dictionary_preview import compare_dictionary
from app.domains.admin_assistant.materials import Material, validate_attachments
from app.domains.capability.publish import save_dictionary
from app.domains.capability.schemas import (
    DictionaryDefinition,
    DictionaryPreview,
    DictionaryRubric,
    SaveDictionary,
)
from app.domains.capability.service import preview_version, read_editor
from app.domains.change_request.schemas import ToolInput


class MaterialCapability(DictionaryRubric):
    key: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9_]+$")
    name: str = Field(min_length=1, max_length=200)
    category: str = Field(max_length=200)
    description: str = Field(max_length=4000)


class MaterialArgs(ToolInput):
    materials: list[Material] = Field(min_length=1, max_length=20)
    mode: Literal["replace", "merge"]
    capabilities: list[MaterialCapability] = Field(min_length=1, max_length=300)


def prepare(db: Session, p: Principal, args: MaterialArgs) -> SaveDictionary:
    require_admin(p)
    editor = read_editor(db, p)
    proposed = [
        DictionaryDefinition.model_validate(
            c.model_dump(exclude={"levels"}) | {"level_rubric": {"levels": c.levels}}
        )
        for c in args.capabilities
    ]
    # Validate duplicates before merging, so repeated keys cannot silently overwrite.
    body = SaveDictionary(
        definitions=proposed, expected_version=editor.expected_version
    )
    if args.mode == "merge":
        definitions = {d.key: d for d in editor.definitions}
        definitions.update((d.key, d) for d in proposed)
        body = SaveDictionary(
            definitions=list(definitions.values()),
            expected_version=editor.expected_version,
        )
    return body


def preview(db: Session, p: Principal, args: MaterialArgs) -> DictionaryPreview:
    return preview_version(db, p, prepare(db, p, args).definitions, [])


def risk(db: Session, p: Principal, args: MaterialArgs) -> Literal["normal", "high"]:
    changes = preview(db, p, args)
    return (
        "high"
        if args.mode == "replace"
        or len(changes.added) + len(changes.modified) + len(changes.removed) >= 10
        else "normal"
    )


def impact(db: Session, p: Principal, args: MaterialArgs) -> BaseModel:
    changes = preview(db, p, args)
    names = {d.key: d.name for d in read_editor(db, p).definitions}
    names.update((d.key, d.name) for d in changes.definitions)
    keys = changes.added + changes.modified + changes.removed
    text = (
        f"新增 {len(changes.added)} 项、修改 {len(changes.modified)} 项、删除 {len(changes.removed)} 项"
        + "；"
        + "、".join(names[k] for k in keys[:20])
    )
    for key in changes.removed:
        count = changes.removed_claim_counts[key]
        pending = changes.removed_item_counts[key]
        if count or pending:
            text += f"；删除「{names[key]}」：{count} 条判断保留，{pending} 条待提交改动项将移除"
    return RootModel(
        {
            "count": len(keys),
            "text": text,
            "dictionary": compare_dictionary(
                read_editor(db, p).definitions, changes.definitions
            ).model_dump(),
        }
    )


def apply(
    db: Session, p: Principal, args: MaterialArgs, *, commit: bool = True
) -> BaseModel:
    require_admin(p)
    context = db.info["audit_context"]
    validate_attachments(db, p, args.materials, UUID(context["assistant_session_id"]))
    body = prepare(db, p, args)
    db.info["audit_context"] = {
        **context,
        "material_names": [m.label for m in args.materials],
    }
    try:
        return save_dictionary(db, p, body, commit=commit)
    finally:
        db.info["audit_context"] = context
