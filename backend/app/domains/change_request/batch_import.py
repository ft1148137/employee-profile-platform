from typing import Annotated, Literal

from pydantic import BaseModel, EmailStr, Field
from sqlmodel import Session, col, select

from app.authz.deps import OrgAdminRequired
from app.authz.principal import Principal
from app.domains.audit.service import record_audit
from app.domains.capability.service import get_schema, level_exists, list_capabilities
from app.domains.change_request.claim import ClaimAfter
from app.domains.change_request.import_json import InvalidImport
from app.domains.change_request.models import (
    ChangeItem,
    ChangeOp,
    ProfileChangeRequest,
    RequestOrigin,
    RequestStatus,
)
from app.domains.change_request.schemas import ToolInput
from app.domains.drafting.service import current_claim_ids
from app.domains.inbox.models import InboxKind
from app.domains.inbox.service import notify
from app.domains.org.models import Department, Employee, EmploymentStatus, Organization
from app.domains.profile.models import AssessmentStatus, ProfileVersion


class MaterialItem(ToolInput):
    capability_key: str = Field(min_length=1, max_length=100)
    statement: str = Field(min_length=1, max_length=200)
    level: str | None = Field(default=None, min_length=1, max_length=100)
    basis: str = Field(min_length=1, max_length=300)
    uncertainty: str | None = Field(default=None, max_length=300)


class MaterialPerson(ToolInput):
    employee_email: Annotated[EmailStr, Field(max_length=254)]
    items: list[MaterialItem] = Field(min_length=1, max_length=20)


class ImportResult(BaseModel):
    display_name: str
    employee_email: str
    department_name: str
    item_count: int
    status: Literal["will_create", "created", "skipped"]
    reason: str | None = None


def import_profiles(
    principal: Principal,
    session: Session,
    people: list[MaterialPerson],
    *,
    material_names: list[str],
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
    filename = "、".join(material_names)[:200]
    for person in people:
        subject = subjects[person.employee_email.casefold()]
        existing = session.exec(
            select(ProfileChangeRequest.id).where(
                ProfileChangeRequest.org_id == org_id,
                ProfileChangeRequest.subject_employee_id == subject.id,
                col(ProfileChangeRequest.status).in_(
                    [RequestStatus.DRAFT, RequestStatus.PENDING_OWNER]
                ),
            )
        ).first()
        result = ImportResult(
            display_name=subject.display_name,
            employee_email=person.employee_email,
            department_name=departments.get(subject.department_id, "未分配部门")
            if subject.department_id
            else "未分配部门",
            item_count=len(person.items),
            status="skipped" if existing else "created" if confirm else "will_create",
            reason="已有草稿或待本人过目的请求，不覆盖" if existing else None,
        )
        results.append(result)
        if existing or not confirm:
            continue
        version = session.exec(
            select(ProfileVersion)
            .where(
                ProfileVersion.org_id == org_id,
                ProfileVersion.subject_employee_id == subject.id,
            )
            .order_by(col(ProfileVersion.revision).desc())
        ).first()
        request = ProfileChangeRequest(
            org_id=org_id,
            subject_employee_id=subject.id,
            origin=RequestOrigin.IMPORT,
            status=RequestStatus.PENDING_OWNER,
            author={
                "actor_type": principal.actor_type,
                "user_id": str(principal.user_id),
            },
            client_name=filename,
            generated_by={
                **session.info.get("audit_context", {}),
                "materials": list(material_names),
            },
            base_version_id=version.id if version else None,
        )
        session.add(request)
        session.flush()
        current = current_claim_ids(session, org_id, subject.id)
        for position, item in enumerate(person.items):
            target = current.get(item.capability_key)
            session.add(
                ChangeItem(
                    org_id=org_id,
                    request_id=request.id,
                    position=position,
                    op=ChangeOp.MODIFY if target else ChangeOp.ADD,
                    target_claim_id=target,
                    capability_definition_id=capabilities[item.capability_key].id,
                    after=ClaimAfter(
                        statement=item.statement,
                        level=item.level,
                        assessment_status=AssessmentStatus.TENTATIVE,
                    ).model_dump(mode="json"),
                    owner_statement=item.basis,
                    uncertainty=item.uncertainty,
                )
            )
        record_audit(
            session,
            org_id=org_id,
            actor_type=principal.actor_type,
            actor_id=str(principal.user_id),
            action="profile_import.created",
            target_type="profile_change_request",
            target_id=str(request.id),
            details={"filename": filename},
        )
        notify(
            session,
            request,
            InboxKind.INITIAL_REVIEW,
            "管理员提供了材料并整理了画像，请过目后提交",
        )
    if confirm:
        session.commit() if commit else session.flush()
    return results
