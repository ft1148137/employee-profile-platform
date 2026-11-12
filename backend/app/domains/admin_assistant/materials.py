"""Model-structured materials create owner-review requests only after confirmation."""

from uuid import UUID

from pydantic import BaseModel, Field, RootModel
from sqlmodel import Session

from app.authz.principal import Principal
from app.domains.bot.attachments import AttachmentError, get_session_attachment
from app.domains.change_request.batch_import import (
    ImportResult,
    MaterialPerson,
    import_profiles,
)
from app.domains.change_request.import_json import InvalidImport
from app.domains.change_request.schemas import ToolInput


class Material(ToolInput):
    attachment_id: UUID | None = None
    label: str = Field(min_length=1, max_length=200)


class MaterialArgs(ToolInput):
    materials: list[Material] = Field(min_length=1, max_length=20)
    people: list[MaterialPerson] = Field(min_length=1, max_length=50)


def validate_attachments(
    db: Session, p: Principal, materials: list[Material], session_id: UUID
) -> None:
    for material in materials:
        if material.attachment_id is not None:
            try:
                get_session_attachment(
                    db, p, "admin", session_id, material.attachment_id
                )
            except AttachmentError:
                raise InvalidImport(
                    ["attachment_id：附件不属于当前会话或已过期"]
                ) from None


def preview(db: Session, p: Principal, args: MaterialArgs) -> list[ImportResult]:
    return import_profiles(
        p,
        db,
        args.people,
        material_names=[m.label for m in args.materials],
        confirm=False,
        commit=False,
    )


def impact(db: Session, p: Principal, args: MaterialArgs) -> BaseModel:
    from app.domains.admin_assistant.schemas import Impact

    people = preview(db, p, args)
    return Impact(
        count=len(people),
        text="；".join(
            f"{r.display_name}（{r.department_name}）：{r.item_count} 条判断"
            + (f"，跳过：{r.reason}" if r.status == "skipped" else "，交本人过目")
            for r in people
        ),
    )


def apply(
    db: Session, p: Principal, args: MaterialArgs, *, commit: bool = True
) -> BaseModel:
    context = db.info["audit_context"]
    validate_attachments(db, p, args.materials, UUID(context["assistant_session_id"]))
    return RootModel(
        import_profiles(
            p,
            db,
            args.people,
            material_names=[m.label for m in args.materials],
            confirm=True,
            commit=commit,
        )
    )
