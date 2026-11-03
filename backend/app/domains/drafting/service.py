from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import BaseModel, JsonValue
from sqlmodel import Session, col, delete, select

from app.authz.principal import Principal
from app.core.db import engine
from app.domains.audit.service import record_audit
from app.domains.base import utc_now
from app.domains.capability.models import CapabilityDefinition
from app.domains.capability.service import SchemaNotFound, get_schema, list_capabilities
from app.domains.capability.templates import InvalidTemplate
from app.domains.change_request.models import (
    ChangeItem,
    ChangeItemEvidence,
    ChangeOp,
    ProfileChangeRequest,
    RequestOrigin,
    RequestStatus,
)
from app.domains.drafting import rules
from app.domains.drafting.collect import collect, evidence_stats
from app.domains.drafting.rules import DraftItem
from app.domains.model.service import read_setting
from app.domains.org.models import Employee, EmploymentStatus, Organization
from app.domains.profile.models import (
    AssessmentStatus,
    EvidenceRelation,
    InferenceStatus,
    ProfileClaim,
    ProfileProgress,
    ProfileVersion,
)


def postprocess(
    session: Session,
    org_id: UUID,
    employee: Employee,
    items: list[DraftItem],
    capabilities: list[CapabilityDefinition],
) -> list[DraftItem]:
    records = {r.evidence.id: r for r in collect(session, org_id, employee)}
    definitions = {c.id: c for c in capabilities}
    result = []
    for item in items:
        capability = definitions[item.capability_id]
        levels = (capability.level_rubric or {}).get("levels", [])
        if item.after.level is not None and (
            not isinstance(levels, list)
            or item.after.level
            not in {level["key"] for level in levels if isinstance(level, dict)}
        ):
            raise InvalidDraft("起草结果使用了词典以外的等级")
        item.evidence = {
            id_: relation
            for id_, relation in item.evidence.items()
            if id_ in records
            and (
                relation != EvidenceRelation.SUPPORTS
                or rules.relation(records[id_]) == EvidenceRelation.SUPPORTS
            )
        }
        supports = [
            id_
            for id_, relation in item.evidence.items()
            if relation == EvidenceRelation.SUPPORTS
        ]
        if not supports:
            continue
        stats = evidence_stats(session, org_id, supports)
        item.after.assessment_status = AssessmentStatus.TENTATIVE
        item.after.evidence_count, item.after.source_count = (
            stats.evidence_count,
            stats.source_count,
        )
        item.after.latest_evidence_at = stats.latest_evidence_at
        names = "、".join(
            "Jira" if source == "jira" else source.removeprefix("csv:")
            for source in stats.sources
        )
        latest = (
            f"最近一次是 {stats.latest_evidence_at.astimezone(ZoneInfo('Asia/Shanghai')).date()}"
            if stats.latest_evidence_at
            else "最近一次的时间未知"
        )
        item.explanation = (
            f"依据 {stats.evidence_count} 项工作经历（同一项目在多个来源出现时合并计算），来自 {stats.source_count} 个来源（{names}），{latest}。"
            + item.explanation
        )
        result.append(item)
    return result


class InvalidDraft(ValueError):
    pass


def write_initial_request(
    session: Session,
    org_id: UUID,
    employee: Employee,
    items: list[DraftItem],
    generated_by: dict[str, JsonValue],
) -> bool:
    if employee.org_id != org_id:
        raise InvalidDraft("员工不属于当前企业")
    request = session.exec(
        select(ProfileChangeRequest)
        .where(
            ProfileChangeRequest.org_id == org_id,
            ProfileChangeRequest.subject_employee_id == employee.id,
            ProfileChangeRequest.origin == RequestOrigin.INITIAL_SYNC,
        )
        .with_for_update()
    ).one_or_none()
    if request and request.status != RequestStatus.PENDING_OWNER:
        return False
    if request is None:
        version = session.exec(
            select(ProfileVersion)
            .where(
                ProfileVersion.org_id == org_id,
                ProfileVersion.subject_employee_id == employee.id,
            )
            .order_by(col(ProfileVersion.revision).desc())
        ).first()
        request = ProfileChangeRequest(
            org_id=org_id,
            subject_employee_id=employee.id,
            origin=RequestOrigin.INITIAL_SYNC,
            status=RequestStatus.PENDING_OWNER,
            author={"actor_type": "system_worker"},
            base_version_id=version.id if version else None,
        )
        session.add(request)
        session.flush()
    old_items = select(ChangeItem.id).where(
        ChangeItem.org_id == org_id, ChangeItem.request_id == request.id
    )
    session.exec(
        delete(ChangeItemEvidence).where(
            col(ChangeItemEvidence.org_id) == org_id,
            col(ChangeItemEvidence.item_id).in_(old_items),
        )
    )
    session.exec(
        delete(ChangeItem).where(
            col(ChangeItem.org_id) == org_id, col(ChangeItem.request_id) == request.id
        )
    )
    claims = list(
        session.exec(
            select(ProfileClaim)
            .where(
                ProfileClaim.org_id == org_id,
                ProfileClaim.subject_employee_id == employee.id,
            )
            .order_by(col(ProfileClaim.created_at), col(ProfileClaim.id))
        )
    )
    superseded = {c.supersedes_claim_id for c in claims}
    current = {
        c.capability_definition_id: c.id for c in claims if c.id not in superseded
    }
    request.generated_by, request.updated_at = generated_by, utc_now()
    session.add(request)
    for position, item in enumerate(items):
        target = current.get(item.capability_id)
        row = ChangeItem(
            org_id=org_id,
            request_id=request.id,
            position=position,
            op=ChangeOp.MODIFY if target else ChangeOp.ADD,
            capability_definition_id=item.capability_id,
            target_claim_id=target,
            after=item.after.model_dump(mode="json"),
            explanation=item.explanation,
            uncertainty=item.uncertainty,
        )
        session.add(row)
        session.flush()
        session.add_all(
            [
                ChangeItemEvidence(
                    org_id=org_id, item_id=row.id, evidence_id=id_, relation=relation
                )
                for id_, relation in item.evidence.items()
            ]
        )
    session.flush()
    return True


def progress_row(
    session: Session,
    org_id: UUID,
    employee_id: UUID,
    status: InferenceStatus,
    error: str | None = None,
) -> None:
    row = session.get(ProfileProgress, (org_id, employee_id)) or ProfileProgress(
        org_id=org_id, employee_id=employee_id
    )
    row.inference_status, row.inference_error, row.updated_at = status, error, utc_now()
    session.add(row)


def active_employees(session: Session, org_id: UUID) -> list[Employee]:
    return list(
        session.exec(
            select(Employee)
            .where(
                Employee.org_id == org_id,
                Employee.employment_status == EmploymentStatus.ACTIVE,
            )
            .order_by(col(Employee.id))
        )
    )


def run_drafting(principal: Principal, employee_ids: list[str] | None) -> None:
    org_id = principal.org_id
    with Session(engine) as session:
        ids = [
            e.id
            for e in active_employees(session, org_id)
            if employee_ids is None or str(e.id) in employee_ids
        ]
    counts = {"generated": 0, "failed": 0, "handed_over": 0}
    if employee_ids is not None and not ids:
        return
    for employee_id in ids:
        with Session(engine) as session:
            progress_row(session, org_id, employee_id, InferenceStatus.RUNNING)
            session.commit()
            try:
                status = session.exec(
                    select(ProfileChangeRequest.status).where(
                        ProfileChangeRequest.org_id == org_id,
                        ProfileChangeRequest.subject_employee_id == employee_id,
                        ProfileChangeRequest.origin == RequestOrigin.INITIAL_SYNC,
                    )
                ).first()
                if status is not None and status != RequestStatus.PENDING_OWNER:
                    progress_row(
                        session, org_id, employee_id, InferenceStatus.GENERATED
                    )
                    session.commit()
                    counts["handed_over"] += 1
                    continue
                employee = session.exec(
                    select(Employee).where(
                        Employee.org_id == org_id,
                        Employee.id == employee_id,
                        Employee.employment_status == EmploymentStatus.ACTIVE,
                    )
                ).one()
                schema = get_schema(session, org_id)
                capabilities = list_capabilities(session, org_id, schema.id)
                keywords, ref = rules.load_keywords(schema.template_ref)
                items = postprocess(
                    session,
                    org_id,
                    employee,
                    rules.draft(
                        collect(session, org_id, employee), capabilities, keywords
                    ),
                    capabilities,
                )
                names = {c.id: c.name for c in capabilities}
                for item in items:
                    item.after.statement = f"有 {item.after.evidence_count} 项与「{names[item.capability_id]}」相关的工作记录"
                written = write_initial_request(
                    session,
                    org_id,
                    employee,
                    items,
                    {
                        "mode": "rules",
                        "schema_version_id": str(schema.id),
                        "keywords_ref": ref,
                        "model": None,
                        "prompt_version": None,
                    },
                )
                progress_row(session, org_id, employee_id, InferenceStatus.GENERATED)
                session.commit()
                counts["generated" if written else "handed_over"] += 1
            except Exception as error:
                # Never expose record contents or database error parameters in progress.
                session.rollback()
                progress_row(
                    session,
                    org_id,
                    employee_id,
                    InferenceStatus.FAILED,
                    str(error)
                    if isinstance(error, (InvalidDraft, InvalidTemplate))
                    else "起草失败，请重试；若仍失败请联系管理员",
                )
                session.commit()
                counts["failed"] += 1
    with Session(engine) as session:
        record_audit(
            session,
            org_id=org_id,
            actor_type=principal.actor_type,
            actor_id=None,
            action="initial_profiles.drafted",
            target_type="organization",
            target_id=str(org_id),
            details={"mode": "rules", **counts},
        )
        session.commit()


def queue_drafting(
    session: Session, org_id: UUID, scope: Literal["all", "failed"]
) -> int:
    from app.worker.tasks import draft_initial

    session.exec(
        select(Organization).where(Organization.id == org_id).with_for_update()
    ).one()
    try:
        get_schema(session, org_id)
    except SchemaNotFound:
        raise HTTPException(422, "请先启用能力词典") from None
    if session.exec(
        select(ProfileProgress).where(
            ProfileProgress.org_id == org_id,
            col(ProfileProgress.inference_status).in_(
                [InferenceStatus.PENDING, InferenceStatus.RUNNING]
            ),
        )
    ).first():
        raise HTTPException(409, "已有起草任务正在排队或执行")
    failed = set(
        session.exec(
            select(ProfileProgress.employee_id).where(
                ProfileProgress.org_id == org_id,
                ProfileProgress.inference_status == InferenceStatus.FAILED,
            )
        )
    )
    ids = [
        e.id
        for e in active_employees(session, org_id)
        if scope == "all" or e.id in failed
    ]
    for id_ in ids:
        progress_row(session, org_id, id_, InferenceStatus.PENDING)
    session.flush()
    if ids:
        connection = session.connection().connection.driver_connection
        draft_initial.configure(
            connection=connection, lock=f"initial-profiles:{org_id}"
        ).defer(org_id=str(org_id), employee_ids=[str(id_) for id_ in ids])
    session.commit()
    return len(ids)


class FailedEmployee(BaseModel):
    employee_id: UUID
    display_name: str
    error: str | None


class DraftProgress(BaseModel):
    mode: Literal["rules", "model"]
    counts: dict[str, int]
    failed: list[FailedEmployee]
    requests: dict[str, int]


def read_progress(session: Session, org_id: UUID) -> DraftProgress:
    employees = {e.id: e for e in active_employees(session, org_id)}
    rows = list(
        session.exec(
            select(ProfileProgress).where(
                ProfileProgress.org_id == org_id,
                col(ProfileProgress.employee_id).in_(employees),
            )
        )
    )
    counts = {
        "not_started": len(employees) - len(rows),
        **{
            s.value: sum(r.inference_status == s for r in rows) for s in InferenceStatus
        },
    }
    requests = list(
        session.exec(
            select(ProfileChangeRequest).where(
                ProfileChangeRequest.org_id == org_id,
                col(ProfileChangeRequest.subject_employee_id).in_(employees),
                ProfileChangeRequest.origin == RequestOrigin.INITIAL_SYNC,
            )
        )
    )
    pending = sum(r.status == RequestStatus.PENDING_OWNER for r in requests)
    setting = read_setting(session, org_id)
    return DraftProgress(
        mode="model" if setting and setting.enabled else "rules",
        counts=counts,
        failed=[
            FailedEmployee(
                employee_id=r.employee_id,
                display_name=employees[r.employee_id].display_name,
                error=r.inference_error,
            )
            for r in rows
            if r.inference_status == InferenceStatus.FAILED
        ],
        requests={"pending_owner": pending, "handed_over": len(requests) - pending},
    )
