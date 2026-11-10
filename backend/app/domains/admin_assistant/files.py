"""File previews stay in encrypted UI messages; the model receives summaries."""

import json
import re
from base64 import b64decode, b64encode
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from fastapi import UploadFile
from pydantic import BaseModel, ConfigDict, Field, JsonValue, RootModel, SecretStr
from sqlmodel import Session, select

from app.authz.principal import Principal
from app.core.secrets import encrypt_secret
from app.domains.admin_assistant import store
from app.domains.admin_assistant.models import AdminAssistantSession
from app.domains.bot import store as messages
from app.domains.capability.files import parse_dictionary
from app.domains.capability.schemas import SaveDictionary
from app.domains.capability.service import preview_version
from app.domains.change_request.batch_import import import_profiles
from app.domains.change_request.import_json import InvalidImport
from app.domains.org.models import Organization, RosterImport
from app.domains.org.roster import service as roster
from app.domains.org.roster.diff import compute_roster_diff
from app.domains.org.roster.schemas import (
    ApplyImport,
    ParsedRoster,
    RosterImportPreview,
)
from app.domains.source.import_records import import_work_records, preview_work_records

if TYPE_CHECKING:
    from app.domains.admin_assistant.registry import AdminFunction, Area, Handler, Risk
    from app.domains.admin_assistant.schemas import AdminTurnResponse

FileKind = Literal["roster", "profile_json", "work_csv", "capabilities"]


class FileError(BaseModel):
    location: str
    message: str


class FileSummary(BaseModel):
    counts: dict[str, int]
    errors: list[FileError] = Field(default_factory=list)


class FileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(max_length=200)
    content: SecretStr


def apply_profiles(
    db: Session, p: Principal, args: FileArgs, *, commit: bool = True
) -> BaseModel:
    return RootModel(
        import_profiles(
            p,
            db,
            [(args.filename, b64decode(args.content.get_secret_value()))],
            confirm=True,
            commit=commit,
        )
    )


def apply_work(
    db: Session, p: Principal, args: FileArgs, *, commit: bool = True
) -> BaseModel:
    return import_work_records(
        db,
        p,
        b64decode(args.content.get_secret_value()),
        args.filename,
        commit=commit,
    )


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


def register_files(functions: dict[str, AdminFunction]) -> None:
    from app.domains.admin_assistant.registry import AdminFunction

    functions["apply_roster_import"] = AdminFunction(
        "apply_roster_import",
        "people",
        "change",
        "normal",
        "确认导入已上传的名单，不能撤销；离职两人及以上需要再次确认",
        RosterArgs,
        apply,
        "/admin/people?tab=roster",
        (("POST", "/roster/imports"), ("POST", "/roster/imports/{id}/apply")),
        snapshot=snapshot,
        impact=impact,
        undoable=False,
        preview_risk=risk,
    )
    entries: list[tuple[str, str, Area, Handler, str, str]] = [
        (
            "import_profiles",
            "导入已上传的批量画像，先交本人过目，不能撤销",
            "rules",
            apply_profiles,
            "/admin/rules?tab=profile-import",
            "/profile-imports",
        ),
        (
            "import_work_records",
            "导入已上传的工作记录，不能撤销",
            "data_ai",
            apply_work,
            "/admin/data-ai?tab=sources",
            "/sources/csv/imports",
        ),
    ]
    for name, title, area, handler, page, endpoint in entries:
        functions[name] = AdminFunction(
            name,
            area,
            "change",
            "normal",
            title,
            FileArgs,
            handler,
            page,
            (("POST", endpoint),),
            file_field="content",
            undoable=False,
        )


def summary(preview: RosterImportPreview) -> dict[str, JsonValue]:
    return {
        "counts": dict[str, JsonValue](preview.summary),
        "errors": [
            {"row_number": i.row_number, "message": i.message}
            for i in [i for i in preview.issues if i.severity == "error"][:20]
        ],
    }


def model_context(
    db: Session, session: AdminAssistantSession
) -> list[dict[str, JsonValue]]:
    for message in reversed(messages.messages_for(db, session, limit=20)):
        if message.role != "assistant" or not isinstance(message.payload, dict):
            continue
        cards = message.payload.get("messages")
        if not isinstance(cards, list):
            continue
        for card in cards:
            if isinstance(card, dict) and card.get("type") == "file_preview":
                if card.get("kind") == "roster":
                    data = summary(RosterImportPreview.model_validate(card["preview"]))
                else:
                    preview = FileSummary.model_validate(card["preview"])
                    preview.errors = preview.errors[:20]
                    data = preview.model_dump(mode="json")
                return [{"type": "file_preview", "preview": data}]
    return []


def upload(
    db: Session,
    p: Principal,
    session_id: UUID,
    kind: FileKind,
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
    limit_mb = {"roster": 5, "profile_json": 1, "work_csv": 5, "capabilities": 2}[kind]
    content = file.file.read(limit_mb * 1024 * 1024 + 1)
    if len(content) > limit_mb * 1024 * 1024:
        raise roster.RosterError(413, f"文件超过 {limit_mb} MB，请拆分后再上传")
    if kind != "roster":
        return _upload_other(db, p, session_id, kind, content, file.filename or "")
    preview = roster.create_import(db, p, content, file.filename or "", commit=False)
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


def _upload_other(
    db: Session,
    p: Principal,
    session_id: UUID,
    kind: FileKind,
    content: bytes,
    filename: str,
) -> AdminTurnResponse:
    from app.domains.admin_assistant import change_set
    from app.domains.admin_assistant.schemas import (
        AdminTurnResponse,
        ChangeSetCard,
        FilePreviewCard,
        ProposeChanges,
    )

    preview = FileSummary(counts={})
    filename = filename.replace("\\", "/").rsplit("/", 1)[-1][:200]
    args: dict[str, JsonValue] = {"filename": filename}
    function = "import_profiles" if kind == "profile_json" else "import_work_records"
    if kind == "profile_json":
        try:
            people = import_profiles(
                p, db, [(filename, content)], confirm=False, commit=False
            )
            preview.counts = {
                "涉及人数": len(people),
                "改动条数": sum(i.item_count for i in people),
                "将创建": sum(i.status == "will_create" for i in people),
                "跳过": sum(i.status == "skipped" for i in people),
            }
        except InvalidImport as error:
            preview.errors = [
                FileError(
                    location=m.group()
                    if (m := re.search(r"第 \d+ (?:个人|行)(?: 第 \d+ 项)?", text))
                    else "",
                    message=text,
                )
                for text in error.messages
            ]
    elif kind == "work_csv":
        parsed = preview_work_records(content, filename)
        preview.counts = {
            "记录条数": len(parsed.rows),
            "涉及人数": len(
                {
                    (r.source, person.account)
                    for r in parsed.rows
                    for person in [r.actor, *r.participants]
                }
            ),
        }
        preview.errors = [
            FileError(
                location=f"第 {i.row_number} 行" if i.row_number else "",
                message=i.message,
            )
            for i in parsed.issues
            if i.level == "error"
        ]
    else:
        definitions, errors = parse_dictionary(content, filename)
        dictionary = preview_version(db, p, definitions, errors)
        preview.counts = {
            "能力条数": len(definitions),
            "新增": len(dictionary.added),
            "修改": len(dictionary.modified),
            "删除": len(dictionary.removed),
            "涉及已发布判断": sum(dictionary.removed_claim_counts.values()),
        }
        preview.errors = [FileError(location="", message=e) for e in dictionary.errors]
        function = "import_capability_dictionary"
        if not preview.errors:
            args = SaveDictionary(
                definitions=definitions,
                expected_version=dictionary.expected_version,
                redraft=False,
            ).model_dump(mode="json")
    response = AdminTurnResponse(messages=[FilePreviewCard(kind=kind, preview=preview)])
    if not preview.errors:
        row = change_set.propose(
            db,
            p,
            session_id,
            ProposeChanges.model_validate(
                {"items": [{"function": function, "args": args}]}
            ),
        )
        if kind != "capabilities":
            row.secrets = encrypt_secret(
                json.dumps({"content": b64encode(content).decode("ascii")})
            )
            db.add(row)
        response.messages.append(ChangeSetCard(change_set=change_set.public(row)))
    messages.append_message(
        db,
        store.get_session(db, p, session_id),
        "assistant",
        response.model_dump(mode="json"),
    )
    db.commit()
    return response
