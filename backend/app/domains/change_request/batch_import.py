from typing import Literal

from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import Session, col, select

from app.authz.deps import OrgAdminRequired
from app.authz.principal import Principal
from app.domains.audit.service import record_audit
from app.domains.capability.service import get_schema, level_exists, list_capabilities
from app.domains.change_request.claim import ClaimAfter
from app.domains.change_request.import_json import (
    ImportPerson,
    InvalidImport,
    decode_import,
    validate_person,
)
from app.domains.change_request.models import (
    ChangeItem,
    ChangeOp,
    ProfileChangeRequest,
    RequestOrigin,
    RequestStatus,
)
from app.domains.drafting.service import current_claim_ids
from app.domains.inbox.models import InboxKind
from app.domains.inbox.service import notify
from app.domains.org.models import Employee, EmploymentStatus, Organization
from app.domains.profile.models import AssessmentStatus, ProfileVersion


class ImportResult(BaseModel):
    display_name: str
    employee_email: str
    item_count: int
    status: Literal["will_create", "created", "skipped"]
    reason: str | None = None


def import_profiles(
    principal: Principal,
    session: Session,
    files: list[tuple[str, bytes]],
    *,
    confirm: bool,
) -> list[ImportResult]:
    if not principal.is_org_admin:
        raise OrgAdminRequired()
    errors: list[str] = []
    people: list[tuple[str, str, ImportPerson]] = []
    total = 0
    for filename, content in files:
        filename = (
            filename.replace("\\", "/").rsplit("/", 1)[-1][:200] or "profile.json"
        )
        try:
            raw = decode_import(content)
        except InvalidImport as error:
            errors.extend(f"{filename} {m}" for m in error.messages)
            continue
        entries = raw if isinstance(raw, list) else [raw]
        total += len(entries)
        if not entries:
            errors.append(f"{filename} 文件：至少需要一个人")
        if total > 200:
            errors.append(f"{filename} 文件：所有文件合计最多 200 人")
            continue
        for index, entry in enumerate(entries, 1):
            prefix = f"{filename} 第 {index} 个人"
            try:
                people.append((filename, prefix, validate_person(entry)))
            except InvalidImport as error:
                errors.extend(f"{prefix} {m}" for m in error.messages)
    org_id = principal.org_id
    # Match dictionary activation and drafting lock order before locking employees.
    session.exec(
        select(Organization).where(Organization.id == org_id).with_for_update()
    ).one()
    schema = get_schema(session, org_id)
    capabilities = {c.key: c for c in list_capabilities(session, org_id, schema.id)}
    emails = {p.employee_email.casefold() for _, _, p in people}
    subjects = {
        e.work_email.casefold(): e
        for e in session.exec(
            select(Employee)
            .where(
                Employee.org_id == org_id,
                Employee.employment_status == EmploymentStatus.ACTIVE,
                func.lower(Employee.work_email).in_(emails),
            )
            .order_by(col(Employee.id))
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
        if e.work_email
    }
    seen: set[str] = set()
    for _, prefix, person in people:
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
    for filename, _, person in people:
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
            "管理员导入了 AI 生成的画像，请过目后提交",
        )
    if confirm:
        session.commit()
    else:
        session.rollback()
    return results
