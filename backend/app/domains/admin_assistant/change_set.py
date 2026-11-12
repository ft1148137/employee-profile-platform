"""Only explicit HTTP actions may apply a proposed administrative change."""

import json
from datetime import timedelta
from inspect import isawaitable
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, JsonValue, RootModel, ValidationError
from sqlmodel import Session, col, select

from app.authz.principal import Principal
from app.core.secrets import decrypt_secret, encrypt_secret
from app.domains.account.service import AccountError
from app.domains.admin_assistant import (
    dictionary_materials,
    materials,
    registry,
    roster_materials,
    store,
)
from app.domains.admin_assistant.models import AdminAssistantSession, AdminChangeSet
from app.domains.admin_assistant.outputs import (
    AccountsOutput,
    OneTimeOutput,
    PasswordOutput,
)
from app.domains.admin_assistant.people import DisableArgs
from app.domains.admin_assistant.schemas import (
    AdminTurnResponse,
    ChangeSetApplied,
    ChangeSetCard,
    ChangeSetPublic,
    Impact,
    ProposeChanges,
    SecretInputCard,
)
from app.domains.base import utc_now
from app.domains.bot import store as messages
from app.domains.bot.schemas import TextMessage
from app.domains.capability.proposals import ProposalConflict
from app.domains.capability.service import DictionaryConflict
from app.domains.change_request.import_json import InvalidImport
from app.domains.im.settings import InvalidImSetting
from app.domains.model.service import InvalidModelSetting
from app.domains.org.models import Organization
from app.domains.org.roster.service import RosterError
from app.domains.org.roster.single import Offboard
from app.domains.source.identities import IdentityConflict, InvalidIdentityEmployee
from app.domains.source.import_records import InvalidWorkRecords
from app.domains.source.service import InvalidSourceSetting
from app.domains.source.sync import SyncError


class ChangeSetError(HTTPException):
    def __init__(self, message: str, status_code: int = 409) -> None:
        super().__init__(status_code, message)


def _lock_org(db: Session, principal: Principal) -> None:
    # Manual settings writes take the same lock; snapshots must stay valid until commit.
    db.exec(
        select(Organization.id)
        .where(Organization.id == principal.org_id)
        .with_for_update()
    ).one()
    db.expire_all()


def get(
    db: Session, principal: Principal, id: UUID, *, lock: bool = False
) -> AdminChangeSet:
    query = (
        select(AdminChangeSet)
        .where(
            AdminChangeSet.id == id,
            AdminChangeSet.org_id == principal.org_id,
            AdminChangeSet.created_by_user_id == principal.user_id,
        )
        .execution_options(populate_existing=True)
    )
    row = db.exec(query.with_for_update() if lock else query).one_or_none()
    if row is None or not principal.is_org_admin:
        raise ChangeSetError("找不到这份改动", 404)
    return row


def public(row: AdminChangeSet) -> ChangeSetPublic:
    result = ChangeSetPublic.model_validate(row, from_attributes=True)
    for item in result.items:
        entry = registry.FUNCTIONS.get(item.function)
        item.title = entry.description if entry else "管理改动"
        item.testable = entry is not None and entry.test is not None
    return result


def _with_secrets(
    entry: registry.AdminFunction, args: dict[str, JsonValue], values: dict[str, str]
) -> BaseModel:
    if entry.file_field and not values.get(entry.file_field):
        raise ChangeSetError("文件已失效，请重新上传", 422)
    try:
        return entry.input_model.model_validate(
            {**args, **{f: values[f] for f in entry.private_fields if f in values}}
        )
    except ValueError:
        raise ChangeSetError("请先用安全输入框填写有效的密钥", 422) from None


async def test_connection(
    db: Session, principal: Principal, id: UUID, item: int
) -> registry.ConnectionTestResult:
    row = get(db, principal, id)
    if row.status != "draft":
        raise ChangeSetError("这份改动已结束，请重新起草")
    if item >= len(row.items):
        raise ChangeSetError("找不到这项改动", 422)
    change = public(row).items[item]
    entry = registry.FUNCTIONS[change.function]
    if entry.test is None:
        raise ChangeSetError("这项改动不支持测试连接", 422)
    try:
        values = json.loads(decrypt_secret(row.secrets)) if row.secrets else {}
        args = _with_secrets(entry, change.args, values)
        result = entry.test(db, principal, args)
        if isawaitable(result):
            result = await result
    except Exception:
        # Test failures must never echo an upstream exception or secret value.
        result = registry.ConnectionTestResult(
            ok=False, message="连接失败，请检查配置或稍后重试"
        )
    messages.append_message(
        db,
        store.get_session(db, principal, row.session_id),
        "assistant",
        AdminTurnResponse(
            messages=[
                TextMessage(
                    text="测试连接：成功"
                    if result.ok
                    else f"测试连接：失败，{result.message}"
                )
            ]
        ).model_dump(mode="json"),
    )
    db.commit()
    return result


def list_drafts(
    db: Session,
    principal: Principal,
    function: str,
    status: Literal["draft"] = "draft",
    session_id: UUID | None = None,
) -> list[ChangeSetPublic]:
    if not principal.is_org_admin:
        raise ChangeSetError("找不到这份改动", 404)
    if session_id is not None:
        store.get_session(db, principal, session_id)
    current_session = (
        select(AdminAssistantSession.id)
        .where(
            AdminAssistantSession.org_id == principal.org_id,
            AdminAssistantSession.user_id == principal.user_id,
        )
        .order_by(
            col(AdminAssistantSession.started_at).desc(),
            col(AdminAssistantSession.id).desc(),
        )
        .limit(1)
        .scalar_subquery()
    )
    rows = db.exec(
        select(AdminChangeSet)
        .where(
            AdminChangeSet.org_id == principal.org_id,
            AdminChangeSet.created_by_user_id == principal.user_id,
            AdminChangeSet.session_id
            == (session_id if session_id is not None else current_session),
            AdminChangeSet.status == status,
        )
        .order_by(col(AdminChangeSet.updated_at).desc(), col(AdminChangeSet.id).desc())
    ).all()
    # Keep the whole change set: confirming a page must still confirm all its items.
    return [
        public(row) for row in rows if any(i["function"] == function for i in row.items)
    ]


def _entry(
    name: str, args: dict[str, JsonValue]
) -> tuple[registry.AdminFunction, BaseModel]:
    entry = registry.FUNCTIONS.get(name)
    if name in ("apply_roster_import", "import_capability_dictionary"):
        raise ChangeSetError("这个操作已下线，请重新发起", 422)
    if (
        entry is None
        or entry.kind != "change"
        or "org_id" in args
        or ("user_id" in args and "user_id" not in entry.input_model.model_fields)
    ):
        raise ChangeSetError("改动功能或参数不正确，不能指定身份或企业", 422)
    if entry.file_field and entry.file_field in args:
        raise ChangeSetError("文件内容只能通过上传框提交", 422)
    if set(entry.secret_fields) & args.keys():
        raise ChangeSetError("请用安全输入框填写密钥", 422)
    if name == "update_im_setting" and args.get("platform", "feishu") != "feishu":
        raise ChangeSetError("助手只支持飞书，企业微信和 Telegram 请到网页上修改", 422)
    # Validate ordinary fields now; secret values are validated again at application.
    try:
        return entry, entry.input_model.model_validate(
            {**args, **dict.fromkeys(entry.private_fields, "pending-secure-input")}
        )
    except ValueError as error:
        if name in (
            "draft_profiles_from_material",
            "draft_roster_from_material",
            "draft_dictionary_from_material",
        ) and isinstance(error, ValidationError):
            raise ChangeSetError(
                "；".join(
                    f"{'.'.join(map(str, e['loc']))}：{e['msg'] if name == 'draft_dictionary_from_material' else e['type']}（请按功能参数说明修改）"
                    for e in error.errors()
                ),
                422,
            ) from None
        raise ChangeSetError("参数不正确，请按功能说明填写", 422) from None


def propose(
    db: Session,
    principal: Principal,
    session_id: UUID,
    body: ProposeChanges,
    *,
    allow_unchanged: bool = False,
) -> AdminChangeSet:
    store.get_session(db, principal, session_id)
    row = (
        get(db, principal, body.change_set_id, lock=True)
        if body.change_set_id
        else None
    )
    if row and (row.session_id != session_id or row.status != "draft"):
        raise ChangeSetError("这份改动已结束，请重新起草")
    items: list[dict[str, JsonValue]] = []
    secret_fields: set[str] = set()
    disabled_users: set[UUID] = set()
    offboarded_employees: set[UUID] = set()
    for item in body.items:
        entry = registry.FUNCTIONS.get(item.function)
        supplied = item.args
        if entry and entry.merge_settings and entry.snapshot:
            before = entry.snapshot(db, principal, None).model_dump(
                mode="json", exclude=entry.private_fields
            )
            # A new model kind needs its own destination and defaults, not the old provider.
            if not (
                entry.name == "update_model_setting"
                and "kind" in supplied
                and supplied["kind"] != (before or {}).get("kind")
            ):
                supplied = {**(before or {}), **supplied}
        entry, args = _entry(item.function, supplied)
        if isinstance(
            args,
            materials.MaterialArgs
            | roster_materials.MaterialArgs
            | dictionary_materials.MaterialArgs,
        ):
            if any(i["function"] == entry.name for i in items):
                raise ChangeSetError(
                    "同一份改动只能整理一批词典，请合并后重新起草"
                    if isinstance(args, dictionary_materials.MaterialArgs)
                    else "同一份改动只能整理一批画像或人员变动，请合并人员后重新起草",
                    422,
                )
            materials.validate_attachments(db, principal, args.materials, session_id)
        # Secrets are stored by field for the whole change set; each needs one target.
        if secret_fields.intersection(entry.private_fields):
            raise ChangeSetError(
                "同一份改动不能重复配置连接或导入文件，请分开起草", 422
            )
        secret_fields.update(entry.private_fields)
        if isinstance(args, DisableArgs):
            disabled_users.update(args.user_ids)
        elif isinstance(args, Offboard):
            offboarded_employees.update(args.employee_ids)
        items.append(
            {
                "function": entry.name,
                "args": args.model_dump(
                    mode="json",
                    exclude=entry.private_fields,
                    exclude_unset=not entry.merge_settings,
                ),
                "before": entry.snapshot(db, principal, args).model_dump(
                    mode="json", exclude=entry.private_fields
                )
                if entry.snapshot
                else None,
                "after": args.model_dump(mode="json", exclude=entry.private_fields),
                "impact": entry.impact(db, principal, args).model_dump(mode="json")
                if entry.impact
                else None,
                "risk": entry.preview_risk(db, principal, args)
                if entry.preview_risk
                else entry.risk(args)
                if callable(entry.risk)
                else entry.risk,
                "undoable": entry.undoable,
            }
        )
    dictionary_functions = {
        "draft_dictionary_from_material",
        "update_capability_dictionary",
        "apply_capability_template",
    }
    if (
        any(i["function"] == "draft_dictionary_from_material" for i in items)
        and sum(i["function"] in dictionary_functions for i in items) > 1
    ):
        raise ChangeSetError("一份确认卡只能保存一次词典，请合并后重新起草", 422)
    # shortcut: Secret settings still need cards for key rotation; tighten when proposals can declare it.
    if not allow_unchanged and all(
        registry.FUNCTIONS[str(item["function"])].merge_settings
        and not registry.FUNCTIONS[str(item["function"])].secret_fields
        and item["before"] is not None
        and item["before"] == item["after"]
        for item in items
    ):
        raise ChangeSetError("设置已经是这个值，不需要改动", 422)
    if len(disabled_users) + len(offboarded_employees) >= 2:
        for saved_item in items:
            if saved_item["function"] in (
                "disable_accounts",
                "offboard_employees",
            ):
                saved_item["risk"] = "high"
    if row:
        row.items = items
        row.revision += 1
        row.confirmed_once_at = None
        row.secrets = None
        row.updated_at = utc_now()
    else:
        assert principal.user_id is not None
        row = AdminChangeSet(
            org_id=principal.org_id,
            session_id=session_id,
            created_by_user_id=principal.user_id,
            items=items,
        )
    db.add(row)
    db.flush()
    return row


def _stale(db: Session, row: AdminChangeSet) -> None:
    row.status = "stale"
    row.secrets = None
    row.confirmed_once_at = None
    row.updated_at = utc_now()
    db.add(row)
    db.commit()
    raise ChangeSetError("内容已变化，请重新预览")


def apply(
    db: Session, principal: Principal, id: UUID, revision: int
) -> ChangeSetApplied:
    _lock_org(db, principal)
    row = get(db, principal, id, lock=True)
    if row.status != "draft":
        if any(
            entry.file_field
            for item in row.items
            if (entry := registry.FUNCTIONS.get(str(item["function"])))
        ):
            raise ChangeSetError("文件已失效，请重新上传", 422)
        raise ChangeSetError("这份改动已结束，请重新起草")
    if row.revision != revision:
        _stale(db, row)
    secret_values = json.loads(decrypt_secret(row.secrets)) if row.secrets else {}
    prepared = []
    for item in public(row).items:
        entry, args = _entry(item.function, item.args)
        try:
            snapshot = (
                entry.snapshot(db, principal, args).model_dump(
                    mode="json", exclude=entry.private_fields
                )
                if entry.snapshot
                else None
            )
        except HTTPException as error:
            if error.status_code != 404:
                raise
            _stale(db, row)
        if entry.snapshot and snapshot != item.before:
            _stale(db, row)
        if entry.private_fields:
            args = _with_secrets(entry, item.args, secret_values)
            if (
                entry.rebinds
                and entry.snapshot
                and entry.rebinds(
                    entry.snapshot(db, principal, args).model_dump(mode="json") or {},
                    item.args,
                )
                and not any(secret_values.get(f) for f in entry.secret_fields)
            ):
                raise ChangeSetError("更换了连接目标，请重新填写凭据", 422)
        prepared.append((entry, args))
    original = get(db, principal, row.undo_of_id) if row.undo_of_id else None
    if original and original.status != "applied":
        _stale(db, row)
    if any(item.risk == "high" for item in public(row).items) and (
        row.confirmed_once_at is None
        or utc_now() - row.confirmed_once_at >= timedelta(minutes=5)
    ):
        row.confirmed_once_at = row.updated_at = utc_now()
        db.add(row)
        db.commit()
        return ChangeSetApplied(**public(row).model_dump())
    one_time: list[OneTimeOutput] = []
    notices: list[TextMessage] = []
    db.info["audit_context"] = {
        "via": "admin_assistant",
        "assistant_session_id": str(row.session_id),
        "change_set_id": str(row.id),
    }
    if original:
        db.info["audit_context"]["undo_of_id"] = str(original.id)
    try:
        applied_items = public(row).items
        for item, (entry, args) in zip(applied_items, prepared, strict=True):
            result = entry.handler(db, principal, args, commit=False)
            if not isinstance(result, BaseModel):
                raise ChangeSetError("暂不支持这类改动")
            if item.function == "draft_profiles_from_material" and isinstance(
                result, RootModel
            ):
                skipped = sum(i.status == "skipped" for i in result.root)
                notices.append(
                    TextMessage(text=f"初始画像请求已整理，跳过 {skipped} 人")
                )
            if isinstance(result, PasswordOutput | AccountsOutput):
                one_time.append(result)
                notices.append(
                    TextMessage(
                        text="已重置，初始密码只显示这一次"
                        if isinstance(result, PasswordOutput)
                        else f"已发放 {result.count} 个账号，账号表只能下载这一次"
                    )
                )
            if entry.snapshot and entry.undoable:
                item.after = entry.snapshot(db, principal, args).model_dump(
                    mode="json", exclude=entry.private_fields
                )
        row.items = [
            item.model_dump(mode="json", exclude={"title"}) for item in applied_items
        ]
        row.status = "applied"
        row.secrets = None
        row.applied_at = row.updated_at = utc_now()
        db.add(row)
        if original:
            original.status = "undone"
            original.updated_at = utc_now()
            db.add(original)
        if notices:
            messages.append_message(
                db,
                store.get_session(db, principal, row.session_id),
                "assistant",
                AdminTurnResponse(messages=[*notices]).model_dump(mode="json"),
            )
        db.commit()
    except InvalidImport as error:
        db.rollback()
        raise ChangeSetError(
            "；".join(error.messages) + "；所有改动均已回滚", error.status_code
        ) from None
    except InvalidWorkRecords as error:
        db.rollback()
        reason = "；".join(
            (f"第 {i.row_number} 行：" if i.row_number else "") + i.message
            for i in error.issues
            if i.level == "error"
        )
        raise ChangeSetError(reason + "；所有改动均已回滚", error.status_code) from None
    except (
        AccountError,
        DictionaryConflict,
        ProposalConflict,
        IdentityConflict,
        InvalidIdentityEmployee,
        RosterError,
    ) as error:
        db.rollback()
        detail = error.detail
        if isinstance(detail, list):
            detail = "；".join(
                f"{item['display_name']}：{item['reason']}"
                if isinstance(item, dict)
                else str(item)
                for item in detail
            )
        raise ChangeSetError(f"{detail}；所有改动均已回滚", error.status_code) from None
    except SyncError as error:
        db.rollback()
        raise ChangeSetError(error.message, error.status) from None
    except (InvalidSourceSetting, InvalidImSetting, InvalidModelSetting) as error:
        db.rollback()
        raise ChangeSetError(str(error), 422) from None
    except Exception:
        db.rollback()
        raise ChangeSetError("未能应用，所有改动均已回滚，请检查设置后重试") from None
    finally:
        db.info.pop("audit_context", None)
    return ChangeSetApplied(**public(row).model_dump(), one_time=one_time)


def discard(db: Session, principal: Principal, id: UUID) -> ChangeSetPublic:
    _lock_org(db, principal)
    row = get(db, principal, id, lock=True)
    if row.status not in ("draft", "stale"):
        raise ChangeSetError("这份改动已结束")
    row.status = "discarded"
    row.secrets = None
    row.updated_at = utc_now()
    db.add(row)
    db.commit()
    return public(row)


def undo(db: Session, principal: Principal, id: UUID) -> ChangeSetPublic:
    _lock_org(db, principal)
    row = get(db, principal, id, lock=True)
    if row.status != "applied":
        raise ChangeSetError("这份改动尚未应用或已经撤销")
    items = public(row).items
    if any(
        item.before is None
        or not item.undoable
        or not registry.FUNCTIONS[item.function].undoable
        for item in items
    ):
        raise ChangeSetError("这份改动不能撤销")
    unchanged = True
    secret_cards: list[tuple[str, str]] = []
    reverse_items = []
    for item in items:
        entry, args = _entry(item.function, item.args)
        if entry.snapshot is None:
            raise ChangeSetError("这份改动不能撤销")
        current = entry.snapshot(db, principal, args).model_dump(
            mode="json", exclude=entry.private_fields
        )
        unchanged &= current == item.after
        assert item.before is not None
        function, body = (
            entry.reverse(db, principal, item.before)
            if entry.reverse
            else (item.function, entry.input_model.model_validate(item.before))
        )
        reverse_items.append(
            {
                "function": function,
                "args": body.model_dump(
                    mode="json", exclude=registry.FUNCTIONS[function].private_fields
                ),
            }
        )
        if entry.rebinds and entry.rebinds(current, item.before):
            secret_cards.extend((function, field) for field in entry.secret_fields)
    reverse = propose(
        db,
        principal,
        row.session_id,
        ProposeChanges.model_validate({"items": list(reversed(reverse_items))}),
        allow_unchanged=True,
    )
    reverse.undo_of_id = row.id
    db.add(reverse)
    if secret_cards:
        items = public(reverse).items
        for item in items:
            if any(function == item.function for function, _ in secret_cards):
                item.impact = Impact(
                    count=item.impact.count if item.impact else 0,
                    text="撤销会回到原来的站点，需要重新填写它的凭据；凭据不回滚",
                )
        reverse.items = [item.model_dump(mode="json") for item in items]
    if (
        not unchanged
        or secret_cards
        or any(item.risk == "high" for item in public(reverse).items)
    ):
        messages.append_message(
            db,
            store.get_session(db, principal, row.session_id),
            "assistant",
            AdminTurnResponse(
                messages=[
                    ChangeSetCard(change_set=public(reverse)),
                    *[
                        SecretInputCard(
                            change_set_id=reverse.id,
                            field=field,
                            title="请重新填写原来站点的凭据",
                        )
                        for _, field in secret_cards
                    ],
                ]
            ).model_dump(mode="json"),
        )
    db.commit()
    return (
        apply(db, principal, reverse.id, reverse.revision)
        if unchanged and not secret_cards
        else public(reverse)
    )


def request_secret_input(
    db: Session, principal: Principal, session_id: UUID, function: str, field: str
) -> SecretInputCard:
    store.get_session(db, principal, session_id)
    entry = registry.FUNCTIONS.get(function)
    if entry is None or field == entry.file_field or field not in entry.secret_fields:
        raise ChangeSetError("这个字段不接受安全输入", 422)
    rows = db.exec(
        select(AdminChangeSet)
        .where(
            AdminChangeSet.org_id == principal.org_id,
            AdminChangeSet.created_by_user_id == principal.user_id,
            AdminChangeSet.session_id == session_id,
            AdminChangeSet.status == "draft",
        )
        .order_by(col(AdminChangeSet.updated_at).desc())
    ).all()
    row = next(
        (r for r in rows if any(i["function"] == function for i in r.items)), None
    )
    if row is None:
        raise ChangeSetError("请先起草需要填写密钥的改动", 422)
    return SecretInputCard(
        change_set_id=row.id, field=field, title=f"{entry.description}：安全填写密钥"
    )


def save_secret(
    db: Session, principal: Principal, id: UUID, field: str, value: str
) -> None:
    row = get(db, principal, id, lock=True)
    if row.status != "draft":
        raise ChangeSetError("这份改动已结束")
    if not any(
        field != registry.FUNCTIONS[item.function].file_field
        and field in registry.FUNCTIONS[item.function].secret_fields
        for item in public(row).items
    ):
        raise ChangeSetError("这个字段不接受安全输入", 422)
    values = json.loads(decrypt_secret(row.secrets)) if row.secrets else {}
    values[field] = value
    row.secrets = encrypt_secret(json.dumps(values))
    row.confirmed_once_at = None
    db.add(row)
    db.commit()
