"""Only explicit HTTP actions may apply a proposed administrative change."""

import json
from datetime import timedelta
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, JsonValue
from sqlmodel import Session, col, select

from app.authz.principal import Principal
from app.core.secrets import decrypt_secret, encrypt_secret
from app.domains.admin_assistant import registry, store
from app.domains.admin_assistant.models import AdminChangeSet
from app.domains.admin_assistant.schemas import (
    AdminTurnResponse,
    ChangeSetCard,
    ChangeSetPublic,
    ProposeChanges,
    SecretInputCard,
)
from app.domains.base import utc_now
from app.domains.bot import store as messages
from app.domains.org.models import Organization


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
    return ChangeSetPublic.model_validate(row, from_attributes=True)


def _entry(
    name: str, args: dict[str, JsonValue]
) -> tuple[registry.AdminFunction, BaseModel]:
    entry = registry.FUNCTIONS.get(name)
    if entry is None or entry.kind != "change" or {"org_id", "user_id"} & args.keys():
        raise ChangeSetError("改动功能或参数不正确，不能指定身份或企业", 422)
    if set(entry.secret_fields) & args.keys():
        raise ChangeSetError("请用安全输入框填写密钥", 422)
    # Validate ordinary fields now; secret values are validated again at application.
    return entry, entry.input_model.model_validate(
        {**args, **dict.fromkeys(entry.secret_fields, "pending-secure-input")}
    )


def propose(
    db: Session, principal: Principal, session_id: UUID, body: ProposeChanges
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
    for item in body.items:
        entry, args = _entry(item.function, item.args)
        items.append(
            {
                "function": entry.name,
                "args": args.model_dump(mode="json", exclude=set(entry.secret_fields)),
                "before": entry.snapshot(db, principal, args).model_dump(
                    mode="json", exclude=set(entry.secret_fields)
                )
                if entry.snapshot
                else None,
                "after": args.model_dump(mode="json", exclude=set(entry.secret_fields)),
                "impact": entry.impact(db, principal, args).model_dump(mode="json")
                if entry.impact
                else None,
                "risk": entry.risk(args) if callable(entry.risk) else entry.risk,
            }
        )
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
) -> ChangeSetPublic:
    _lock_org(db, principal)
    row = get(db, principal, id, lock=True)
    if row.status != "draft":
        raise ChangeSetError("这份改动已结束，请重新起草")
    if row.revision != revision:
        _stale(db, row)
    secret_values = json.loads(decrypt_secret(row.secrets)) if row.secrets else {}
    prepared = []
    for item in public(row).items:
        entry, args = _entry(item.function, item.args)
        if (
            entry.snapshot
            and entry.snapshot(db, principal, args).model_dump(
                mode="json", exclude=set(entry.secret_fields)
            )
            != item.before
        ):
            _stale(db, row)
        if entry.secret_fields:
            try:
                args = entry.input_model.model_validate(
                    {
                        **item.args,
                        **{
                            f: secret_values[f]
                            for f in entry.secret_fields
                            if f in secret_values
                        },
                    }
                )
            except ValueError:
                raise ChangeSetError("请先用安全输入框填写有效的密钥", 422) from None
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
        return public(row)
    db.info["audit_context"] = {
        "via": "admin_assistant",
        "assistant_session_id": str(row.session_id),
        "change_set_id": str(row.id),
    }
    try:
        applied_items = public(row).items
        for item, (entry, args) in zip(applied_items, prepared, strict=True):
            result = entry.handler(db, principal, args, commit=False)
            if not isinstance(result, BaseModel):
                raise ChangeSetError("暂不支持这类改动")
            if entry.snapshot:
                item.after = entry.snapshot(db, principal, args).model_dump(
                    mode="json", exclude=set(entry.secret_fields)
                )
        row.items = [item.model_dump(mode="json") for item in applied_items]
        row.status = "applied"
        row.secrets = None
        row.applied_at = row.updated_at = utc_now()
        db.add(row)
        if original:
            original.status = "undone"
            original.updated_at = utc_now()
            db.add(original)
        db.commit()
    except Exception:
        db.rollback()
        raise ChangeSetError("未能应用，所有改动均已回滚，请检查设置后重试") from None
    finally:
        db.info.pop("audit_context", None)
    return public(row)


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
    if any(item.before is None for item in items):
        raise ChangeSetError("这份改动不能撤销")
    unchanged = True
    reverse_items = []
    for item in items:
        entry, args = _entry(item.function, item.args)
        if entry.snapshot is None:
            raise ChangeSetError("这份改动不能撤销")
        unchanged &= (
            entry.snapshot(db, principal, args).model_dump(
                mode="json", exclude=set(entry.secret_fields)
            )
            == item.after
        )
        assert item.before is not None
        function, body = (
            entry.reverse(db, principal, item.before)
            if entry.reverse
            else (item.function, entry.input_model.model_validate(item.before))
        )
        reverse_items.append(
            {"function": function, "args": body.model_dump(mode="json")}
        )
    reverse = propose(
        db,
        principal,
        row.session_id,
        ProposeChanges.model_validate({"items": list(reversed(reverse_items))}),
    )
    reverse.undo_of_id = row.id
    db.add(reverse)
    if not unchanged or any(item.risk == "high" for item in public(reverse).items):
        messages.append_message(
            db,
            store.get_session(db, principal, row.session_id),
            "assistant",
            AdminTurnResponse(
                messages=[ChangeSetCard(change_set=public(reverse))]
            ).model_dump(mode="json"),
        )
    db.commit()
    return (
        apply(db, principal, reverse.id, reverse.revision)
        if unchanged
        else public(reverse)
    )


def request_secret_input(
    db: Session, principal: Principal, session_id: UUID, function: str, field: str
) -> SecretInputCard:
    store.get_session(db, principal, session_id)
    entry = registry.FUNCTIONS.get(function)
    if entry is None or field not in entry.secret_fields:
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
        field in registry.FUNCTIONS[item.function].secret_fields
        for item in public(row).items
    ):
        raise ChangeSetError("这个字段不接受安全输入", 422)
    values = json.loads(decrypt_secret(row.secrets)) if row.secrets else {}
    values[field] = value
    row.secrets = encrypt_secret(json.dumps(values))
    row.confirmed_once_at = None
    db.add(row)
    db.commit()
