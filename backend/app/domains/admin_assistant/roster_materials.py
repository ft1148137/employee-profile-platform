"""Material-derived personnel changes reuse complete-roster validation and application."""

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, RootModel
from sqlmodel import Session

from app.authz.principal import Principal
from app.domains.account.service import require_admin
from app.domains.admin_assistant.materials import Material, validate_attachments
from app.domains.change_request.schemas import ToolInput
from app.domains.org.employee_fields import FIELD_LABELS
from app.domains.org.models import Organization
from app.domains.org.roster import service
from app.domains.org.roster.schemas import ApplyImport, ParsedRoster, RosterRow
from app.domains.org.roster.single import current_roster


class Offboard(ToolInput):
    action: Literal["offboard"]
    employee_code: str = Field(min_length=1, max_length=255)


class RosterFields(ToolInput):
    employee_code: str = Field(min_length=1, max_length=255)
    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    department: str | None = Field(default=None, min_length=1, max_length=1000)
    email: str | None = Field(default=None, min_length=1, max_length=320)
    title: str | None = Field(default=None, max_length=255)
    manager_code: str | None = Field(default=None, max_length=255)
    job_family: str | None = Field(default=None, max_length=50)
    work_location: str | None = Field(default=None, max_length=50)
    hire_date: date | None = None


class Update(RosterFields):
    action: Literal["update"]


class Onboard(RosterFields):
    action: Literal["onboard"]
    display_name: str = Field(min_length=1, max_length=255)
    department: str = Field(min_length=1, max_length=1000)
    email: str = Field(min_length=1, max_length=320)


class MaterialArgs(ToolInput):
    materials: list[Material] = Field(min_length=1, max_length=20)
    rows: list[
        Annotated[Onboard | Update | Offboard, Field(discriminator="action")]
    ] = Field(min_length=1, max_length=500)


def prepare(db: Session, p: Principal, args: MaterialArgs) -> ParsedRoster:
    roster = current_roster(db, p)
    by_code = {r.employee_code.casefold(): r for r in roster.rows}
    seen: set[str] = set()
    departures: set[str] = set()
    for change in args.rows:
        code = change.employee_code.casefold()
        if code in seen or (isinstance(change, Onboard) and code in by_code):
            raise service.RosterError(422, f"工号重复：{change.employee_code}")
        seen.add(code)
        row = by_code.get(code)
        if isinstance(change, Onboard):
            row = RosterRow(
                row_number=len(roster.rows) + 2,
                employee_code=change.employee_code,
                display_name=change.display_name,
                department_path=(),
                title=None,
                manager_code=None,
                email=change.email,
            )
            roster.rows.append(row)
        elif row is None or row.inactive:
            raise service.RosterError(404, "找不到该工号的在职员工")
        if isinstance(change, Offboard):
            departures.add(code)
            continue
        for field, label in FIELD_LABELS.items():
            if field in change.model_fields_set and label not in roster.column_mapping:
                raise service.RosterError(422, f"「{label}」未开启")
        values = change.model_dump(
            exclude_unset=True, exclude={"action", "employee_code", "department"}
        )
        if any(values.get(f, "") is None for f in ("display_name", "email")):
            raise service.RosterError(422, "姓名和邮箱不能为空")
        for field, value in values.items():
            setattr(row, field, value)
        if "department" in change.model_fields_set:
            row.department_path = tuple(
                part.strip()
                for part in (change.department or "").replace("／", "/").split("/")
                if part.strip()
            )
    roster.rows = [
        r for r in roster.rows if r.employee_code.casefold() not in departures
    ]
    for row in roster.rows:
        if row.manager_code and row.manager_code.casefold() in departures:
            row.manager_code = None
    return roster


def snapshot(db: Session, p: Principal, _args: MaterialArgs) -> BaseModel:
    require_admin(p)
    return RootModel(
        {"roster_revision": db.get_one(Organization, p.org_id).roster_revision}
    )


def risk(args: MaterialArgs) -> Literal["normal", "high"]:
    return (
        "high"
        if len(args.rows) >= 2 or any(isinstance(r, Offboard) for r in args.rows)
        else "normal"
    )


def impact(db: Session, p: Principal, args: MaterialArgs) -> BaseModel:
    preview = service.create_import(
        db, p, prepare(db, p, args), "材料预览", persist=False
    )
    changes = preview.changes
    added = {c.employee_code for c in changes if c.kind in ("added", "reactivated")}
    removed = {c.employee_code for c in changes if c.kind == "deactivated"}
    adjusted = {c.employee_code for c in changes} - added - removed
    labels: dict[str, list[str]] = {}
    for c in changes:
        label = (
            "新增"
            if c.employee_code in added
            else "离职"
            if c.employee_code in removed
            else "、".join(
                f"{f.field}：{f.before or '空'} → {f.after or '空'}" for f in c.fields
            )
        )
        labels.setdefault(c.display_name + f"（{c.employee_code}）", []).append(label)
    text = f"新增 {len(added)} 人、调整 {len(adjusted)} 人、离职 {len(removed)} 人"
    text += "；" + "；".join(
        f"{name}：{'；'.join(parts)}" for name, parts in list(labels.items())[:20]
    )
    if len(labels) > 20:
        text += f"；另外 {len(labels) - 20} 人"
    text += "".join(
        f"；第 {i.row_number} 行 {i.column or ''}：{i.message}" for i in preview.issues
    )
    return RootModel({"count": len(added | adjusted | removed), "text": text})


def apply(
    db: Session, p: Principal, args: MaterialArgs, *, commit: bool = True
) -> BaseModel:
    context = db.info["audit_context"]
    validate_attachments(db, p, args.materials, UUID(context["assistant_session_id"]))
    record = service.create_import(
        db,
        p,
        prepare(db, p, args),
        "、".join(m.label for m in args.materials),
        commit=False,
    )
    db.info["audit_context"] = {
        **context,
        "material_names": [m.label for m in args.materials],
    }
    try:
        return service.apply_import(
            db,
            p,
            record.id,
            ApplyImport(
                base_revision=record.base_revision, confirm_mass_deactivation=True
            ),
            commit=commit,
        )
    finally:
        db.info["audit_context"] = context
