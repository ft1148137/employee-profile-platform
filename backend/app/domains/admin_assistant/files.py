"""File previews stay in encrypted UI messages; the model receives summaries."""

import json
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from fastapi import UploadFile
from pydantic import BaseModel, ConfigDict, Field, JsonValue, RootModel
from sqlmodel import Session, select

from app.authz.principal import Principal
from app.domains.admin_assistant import store
from app.domains.bot import store as messages
from app.domains.org.models import Organization, RosterImport
from app.domains.org.roster import service as roster
from app.domains.org.roster.diff import compute_roster_diff
from app.domains.org.roster.schemas import (
    ApplyImport,
    ParsedRoster,
    RosterImportPreview,
)

if TYPE_CHECKING:
    from app.domains.admin_assistant.registry import AdminFunction, Risk
    from app.domains.admin_assistant.schemas import AdminTurnResponse


class RosterArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    import_id: UUID
    base_revision: int = Field(ge=0)


def _record(db: Session, p: Principal, args: RosterArgs) -> RosterImport:
    record = db.exec(
        select(RosterImport).where(
            RosterImport.org_id == p.org_id,
            RosterImport.id == args.import_id,
            RosterImport.uploaded_by_user_id == p.user_id,
        )
    ).one_or_none()
    if record is None or not p.is_org_admin:
        raise roster.RosterError(404, "没有这份名单")
    if record.base_revision != args.base_revision or any(
        i["severity"] == "error" for i in record.issues
    ):
        raise roster.RosterError(422, "名单有错误，请改好后重新上传")
    return record


def snapshot(db: Session, p: Principal, args: RosterArgs) -> BaseModel:
    record = _record(db, p, args)
    revision = db.exec(
        select(Organization.roster_revision).where(Organization.id == p.org_id)
    ).one()
    return RootModel({"roster_revision": revision, "status": record.status})


def risk(db: Session, p: Principal, args: RosterArgs) -> Risk:
    record = _record(db, p, args)
    current = snapshot(db, p, args).model_dump()
    if current != {"roster_revision": args.base_revision, "status": "previewed"}:
        raise roster.RosterError(409, "名单已变化，请重新上传")
    return "high" if record.summary["deactivated"] >= 2 else "normal"


def impact(db: Session, p: Principal, args: RosterArgs) -> BaseModel:
    record = _record(db, p, args)
    counts = record.summary
    diff = compute_roster_diff(
        db,
        p.org_id,
        ParsedRoster.model_validate(
            {
                "rows": record.rows,
                "column_mapping": record.column_mapping,
            }
        ),
    )
    return RootModel(
        {
            "count": len({c.employee_code.casefold() for c in diff.changes}),
            "text": f"新增 {counts['added']} 人，离职并停用 {counts['deactivated']} 人；请核对名单预览",
        }
    )


def apply(
    db: Session, p: Principal, args: RosterArgs, *, commit: bool = True
) -> BaseModel:
    _record(db, p, args)
    return roster.apply_import(
        db,
        p,
        args.import_id,
        ApplyImport(base_revision=args.base_revision, confirm_mass_deactivation=True),
        commit=commit,
    )


def cannot_undo(
    _db: Session, _p: Principal, _before: dict[str, JsonValue]
) -> tuple[str, BaseModel]:
    raise roster.RosterError(409, "名单导入不能撤销，请重新上传完整名单")


def register_files(functions: dict[str, AdminFunction]) -> None:
    from app.domains.admin_assistant.registry import AdminFunction

    functions["apply_roster_import"] = AdminFunction(
        "apply_roster_import",
        "people",
        "change",
        risk,
        "确认导入已上传的名单，不能撤销；离职两人及以上需要再次确认",
        RosterArgs,
        apply,
        "/admin/people?tab=roster",
        (("POST", "/roster/imports"), ("POST", "/roster/imports/{id}/apply")),
        snapshot=snapshot,
        impact=impact,
        reverse=cannot_undo,
    )


def summary(preview: RosterImportPreview) -> dict[str, JsonValue]:
    return {
        "counts": dict[str, JsonValue](preview.summary),
        "errors": [
            {"row_number": i.row_number, "message": i.message}
            for i in [i for i in preview.issues if i.severity == "error"][:20]
        ],
    }


def model_message(text: str) -> str:
    payload = json.loads(text)
    if isinstance(payload, dict):
        for card in payload.get("messages", []):
            if card.get("type") == "file_preview":
                card["preview"] = summary(
                    RosterImportPreview.model_validate(card["preview"])
                )
    return json.dumps(payload, ensure_ascii=False)


def upload(
    db: Session,
    p: Principal,
    session_id: UUID,
    kind: Literal["roster"],
    file: UploadFile,
) -> AdminTurnResponse:
    from app.domains.admin_assistant import change_set
    from app.domains.admin_assistant.schemas import (
        AdminTurnResponse,
        ChangeSetCard,
        FilePreviewCard,
        ProposeChanges,
    )

    session = store.get_session(db, p, session_id)
    content = file.file.read(roster.MAX_FILE_SIZE + 1)
    if len(content) > roster.MAX_FILE_SIZE:
        raise roster.RosterError(413, "文件超过 5 MB，请拆分后再上传")
    preview = roster.create_import(db, p, content, file.filename or "")
    response = AdminTurnResponse(messages=[FilePreviewCard(kind=kind, preview=preview)])
    if preview.can_apply:
        row = change_set.propose(
            db,
            p,
            session_id,
            ProposeChanges.model_validate(
                {
                    "items": [
                        {
                            "function": "apply_roster_import",
                            "args": {
                                "import_id": str(preview.id),
                                "base_revision": preview.base_revision,
                            },
                        }
                    ],
                }
            ),
        )
        response.messages.append(ChangeSetCard(change_set=change_set.public(row)))
    messages.append_message(db, session, "assistant", response.model_dump(mode="json"))
    db.commit()
    return response
