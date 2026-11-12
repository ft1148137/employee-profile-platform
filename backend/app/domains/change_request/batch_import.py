from typing import Annotated, Literal

from pydantic import BaseModel, EmailStr, Field
from sqlmodel import Session, col, select

from app.authz.deps import OrgAdminRequired
from app.authz.principal import Principal
from app.domains.capability.service import get_schema, level_exists, list_capabilities
from app.domains.change_request.import_json import InvalidImport
from app.domains.change_request.models import ChangeOp
from app.domains.change_request.schemas import ToolInput
from app.domains.org.models import Department, Employee, EmploymentStatus, Organization
from app.domains.profile.models import ProfileVersion
from app.domains.review.direct import (
    DirectEditInput,
    DirectEditItem,
    DirectItemInput,
    direct_edit,
    preview_direct_edit,
)
from app.domains.review.publish import current_claims


class MaterialItem(ToolInput):
    capability_key: str = Field(min_length=1, max_length=100)
    statement: str = Field(min_length=1, max_length=200)
    level: str = Field(min_length=1, max_length=100)
    basis: str = Field(min_length=1, max_length=300)
    uncertainty: str | None = Field(default=None, max_length=300)


class MaterialPerson(ToolInput):
    employee_email: Annotated[EmailStr, Field(max_length=254)]
    base_revision: int | None = Field(default=None, ge=1)
    items: list[MaterialItem] = Field(min_length=1, max_length=20)


class ImportResult(BaseModel):
    display_name: str
    employee_email: str
    department_name: str
    item_count: int
    status: Literal["will_publish", "published"]
    changes: list[DirectEditItem]


def import_profiles(
    principal: Principal,
    session: Session,
    people: list[MaterialPerson],
    *,
    confirm: bool,
    commit: bool = True,
) -> list[ImportResult]:
    if not principal.is_org_admin:
        raise OrgAdminRequired()
    errors: list[str] = []
    org_id = principal.org_id
    # Match dictionary activation and drafting lock order before locking employees.
    if confirm:
        session.exec(
            select(Organization).where(Organization.id == org_id).with_for_update()
        ).one()
    schema = get_schema(session, org_id)
    capabilities = {c.key: c for c in list_capabilities(session, org_id, schema.id)}
    emails = {p.employee_email.casefold() for p in people}
    # SQL lower() does not implement the roster's Unicode casefold rules.
    employee_ids = {
        id_
        for id_, email in session.exec(
            select(Employee.id, Employee.work_email).where(
                Employee.org_id == org_id,
                Employee.employment_status == EmploymentStatus.ACTIVE,
            )
        ).all()
        if email and email.casefold() in emails
    }
    employee_query = (
        select(Employee)
        .where(
            Employee.org_id == org_id,
            Employee.employment_status == EmploymentStatus.ACTIVE,
            col(Employee.id).in_(employee_ids),
        )
        .order_by(col(Employee.id))
        .execution_options(populate_existing=True)
    )
    subjects = {
        e.work_email.casefold(): e
        for e in session.exec(
            employee_query.with_for_update() if confirm else employee_query
        ).all()
        if e.work_email
    }
    seen: set[str] = set()
    for index, person in enumerate(people, 1):
        prefix = f"第 {index} 个人"
        email = person.employee_email.casefold()
        if email in seen:
            errors.append(f"{prefix} employee_email：同一批里邮箱不能重复")
        seen.add(email)
        if email not in subjects:
            errors.append(f"{prefix} employee_email：不是本企业在职员工")
        elif subjects[email].id == principal.employee_id:
            errors.append(f"{prefix} employee_email：不能直接修改自己的画像")
        keys: set[str] = set()
        for index, item in enumerate(person.items, 1):
            capability = capabilities.get(item.capability_key)
            label = f"{prefix} 第 {index} 项"
            if capability is None:
                errors.append(f"{label} capability_key：能力 key 不在当前启用的词典中")
            elif not level_exists(capability.level_rubric, item.level):
                errors.append(f"{label} level：等级 key 不在该能力的等级中")
            if item.capability_key in keys:
                errors.append(f"{label} capability_key：同一能力不能重复修改")
            keys.add(item.capability_key)
    if errors:
        raise InvalidImport(errors)
    results = []
    departments = {
        d.id: d.name
        for d in session.exec(
            select(Department).where(Department.org_id == org_id)
        ).all()
    }
    for person in people:
        subject = subjects[person.employee_email.casefold()]
        version = session.exec(
            select(ProfileVersion)
            .where(
                ProfileVersion.org_id == org_id,
                ProfileVersion.subject_employee_id == subject.id,
            )
            .order_by(col(ProfileVersion.revision).desc())
        ).first()
        if not confirm:
            person.base_revision = version.revision if version else None
        current = current_claims(session, org_id, subject.id)
        body = DirectEditInput(
            base_revision=person.base_revision,
            items=[
                DirectItemInput(
                    op=ChangeOp.MODIFY
                    if item.capability_key in current
                    else ChangeOp.ADD,
                    capability_key=item.capability_key,
                    statement=item.statement,
                    level=item.level,
                    explanation=item.basis,
                )
                for item in person.items
            ],
        )
        if confirm:
            result = direct_edit(
                principal,
                session,
                subject.id,
                body,
                via="web",
                client_name="管理助手",
                commit=False,
            )
        else:
            result = preview_direct_edit(principal, session, subject.id, body)
        results.append(
            ImportResult(
                display_name=subject.display_name,
                employee_email=person.employee_email,
                department_name=departments.get(subject.department_id, "未分配部门")
                if subject.department_id
                else "未分配部门",
                item_count=len(person.items),
                changes=result.items,
                status="published" if confirm else "will_publish",
            )
        )
    if confirm:
        session.commit() if commit else session.flush()
    return results
