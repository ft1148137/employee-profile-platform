"""Only explicit HTTP actions may apply a proposed administrative change."""

from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, JsonValue
from sqlmodel import Session, select

from app.authz.principal import Principal
from app.domains.admin_assistant import registry, store
from app.domains.admin_assistant.models import AdminChangeSet
from app.domains.admin_assistant.schemas import ChangeSetPublic, ProposeChanges
from app.domains.base import utc_now
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
    if lock:
        _lock_org(db, principal)
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
    if entry.risk == "high" or entry.secret_fields:
        # shortcut: High-risk changes and secret inputs stay disabled until S5b.
        raise ChangeSetError("暂不支持这类改动，请到对应页面操作", 422)
    return entry, entry.input_model.model_validate(args)


def propose(
    db: Session, principal: Principal, session_id: UUID, body: ProposeChanges
) -> AdminChangeSet:
    store.get_session(db, principal, session_id)
    _lock_org(db, principal)
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
                "args": args.model_dump(mode="json"),
                "before": entry.snapshot(db, principal, args).model_dump(mode="json")
                if entry.snapshot
                else None,
                "after": args.model_dump(mode="json"),
                "impact": entry.impact(db, principal, args).model_dump(mode="json")
                if entry.impact
                else None,
                "risk": entry.risk,
            }
        )
    if row:
        row.items = items
        row.revision += 1
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
    row.updated_at = utc_now()
    db.add(row)
    db.commit()
    raise ChangeSetError("内容已变化，请重新预览")


def apply(
    db: Session, principal: Principal, id: UUID, revision: int
) -> ChangeSetPublic:
    row = get(db, principal, id, lock=True)
    if row.status != "draft":
        raise ChangeSetError("这份改动已结束，请重新起草")
    if row.revision != revision:
        _stale(db, row)
    prepared = []
    for item in public(row).items:
        entry, args = _entry(item.function, item.args)
        if (
            entry.snapshot
            and entry.snapshot(db, principal, args).model_dump(mode="json")
            != item.before
        ):
            _stale(db, row)
        prepared.append((entry, args))
    original = get(db, principal, row.undo_of_id) if row.undo_of_id else None
    if original and original.status != "applied":
        _stale(db, row)
    db.info["audit_context"] = {
        "via": "admin_assistant",
        "assistant_session_id": str(row.session_id),
        "change_set_id": str(row.id),
    }
    try:
        for entry, args in prepared:
            result = entry.handler(db, principal, args, commit=False)
            if not isinstance(result, BaseModel):
                raise ChangeSetError("暂不支持这类改动")
        row.status = "applied"
        row.applied_at = row.updated_at = utc_now()
        db.add(row)
        if original:
            original.status = "undone"
            original.updated_at = utc_now()
            db.add(original)
        db.commit()
    except Exception as error:
        db.rollback()
        raise ChangeSetError("未能应用，所有改动均已回滚，请检查设置后重试") from error
    finally:
        db.info.pop("audit_context", None)
    return public(row)


def discard(db: Session, principal: Principal, id: UUID) -> ChangeSetPublic:
    row = get(db, principal, id, lock=True)
    if row.status not in ("draft", "stale"):
        raise ChangeSetError("这份改动已结束")
    row.status = "discarded"
    row.updated_at = utc_now()
    db.add(row)
    db.commit()
    return public(row)


def undo(db: Session, principal: Principal, id: UUID) -> ChangeSetPublic:
    row = get(db, principal, id, lock=True)
    if row.status != "applied":
        raise ChangeSetError("这份改动尚未应用或已经撤销")
    items = public(row).items
    if any(item.before is None for item in items):
        raise ChangeSetError("这份改动不能撤销")
    unchanged = True
    for item in items:
        entry, args = _entry(item.function, item.args)
        if entry.snapshot is None:
            raise ChangeSetError("这份改动不能撤销")
        unchanged &= (
            entry.snapshot(db, principal, args).model_dump(mode="json") == item.after
        )
    reverse = propose(
        db,
        principal,
        row.session_id,
        ProposeChanges.model_validate(
            {
                "items": [
                    {"function": item.function, "args": item.before}
                    for item in reversed(items)
                ]
            }
        ),
    )
    reverse.undo_of_id = row.id
    db.add(reverse)
    db.commit()
    return (
        apply(db, principal, reverse.id, reverse.revision)
        if unchanged
        else public(reverse)
    )
