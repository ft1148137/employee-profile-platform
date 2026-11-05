from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from procrastinate import App, SyncPsycopgConnector
from pydantic import BaseModel, JsonValue
from sqlalchemy import text
from sqlmodel import Session, col, delete, select

from app.authz.principal import Principal
from app.core.db import engine
from app.core.secrets import SecretUnavailable
from app.domains.audit.service import record_audit
from app.domains.base import utc_now
from app.domains.capability.models import CapabilityDefinition
from app.domains.capability.service import SchemaNotFound, get_schema, list_capabilities
from app.domains.change_request.models import (
    ChangeItem,
    ChangeItemEvidence,
    ChangeOp,
    ProfileChangeRequest,
    RequestOrigin,
    RequestStatus,
)
from app.domains.drafting import model
from app.domains.drafting.collect import (
    TaskGroups,
    collect,
    evidence_stats,
    task_groups,
)
from app.domains.drafting.collect import relation as record_relation
from app.domains.drafting.model import DraftItem
from app.domains.drafting.settings import drafting_scope
from app.domains.inbox.models import InboxKind
from app.domains.inbox.service import notify
from app.domains.model.client import (
    ModelOutputInvalid,
    ModelRateLimited,
    ModelServiceError,
    ModelUnavailable,
)
from app.domains.model.service import model_config, read_setting, validate_capabilities
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
    groups: TaskGroups | None = None,
) -> list[DraftItem]:
    groups = groups if groups is not None else task_groups(session, org_id)
    records = {r.evidence.id: r for r in collect(session, org_id, employee, groups)}
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
                or record_relation(records[id_]) == EvidenceRelation.SUPPORTS
            )
        }
        supports = [
            id_
            for id_, relation in item.evidence.items()
            if relation == EvidenceRelation.SUPPORTS
        ]
        if not supports:
            continue
        stats = evidence_stats(session, org_id, supports, groups)
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


MODEL_REQUIRED = "还没有配置模型，请先在设置页配置模型，或者让员工用 JSON 导入画像"


class ModelRequired(HTTPException):
    def __init__(self) -> None:
        super().__init__(409, MODEL_REQUIRED)


class ModelDisabled(Exception):
    def __init__(self) -> None:
        super().__init__("模型已关闭")


class InvalidDraft(ValueError):
    pass


def current_claim_ids(
    session: Session, org_id: UUID, employee_id: UUID
) -> dict[str, UUID]:
    claims = session.exec(
        select(ProfileClaim, CapabilityDefinition.key)
        .join(
            CapabilityDefinition,
            (col(CapabilityDefinition.id) == ProfileClaim.capability_definition_id)
            & (col(CapabilityDefinition.org_id) == ProfileClaim.org_id),
        )
        .where(
            ProfileClaim.org_id == org_id,
            ProfileClaim.subject_employee_id == employee_id,
        )
        .order_by(col(ProfileClaim.created_at), col(ProfileClaim.id))
    ).all()
    superseded = {c.supersedes_claim_id for c, _ in claims}
    return {key: c.id for c, key in claims if c.id not in superseded}


def write_initial_request(
    session: Session,
    org_id: UUID,
    employee: Employee,
    items: list[DraftItem],
    generated_by: dict[str, JsonValue],
) -> bool:
    if employee.org_id != org_id:
        raise InvalidDraft("员工不属于当前企业")
    session.exec(
        select(Organization).where(Organization.id == org_id).with_for_update()
    ).one()
    active = get_schema(session, org_id)
    active_ids = {c.id for c in list_capabilities(session, org_id, active.id)}
    if any(item.capability_id not in active_ids for item in items):
        raise InvalidDraft("词典已更新，请按当前词典重新起草")
    request = session.exec(
        select(ProfileChangeRequest)
        .where(
            ProfileChangeRequest.org_id == org_id,
            ProfileChangeRequest.subject_employee_id == employee.id,
            ProfileChangeRequest.origin == RequestOrigin.INITIAL_SYNC,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
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
        notify(
            session, request, InboxKind.INITIAL_REVIEW, "初始画像已起草，请过目后提交"
        )
    else:
        request.revision += 1
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
    current = current_claim_ids(session, org_id, employee.id)
    keys = dict(
        session.exec(
            select(CapabilityDefinition.id, CapabilityDefinition.key).where(
                CapabilityDefinition.org_id == org_id,
                col(CapabilityDefinition.id).in_({i.capability_id for i in items}),
            )
        ).all()
    )
    request.generated_by, request.updated_at = generated_by, utc_now()
    session.add(request)
    for position, item in enumerate(items):
        target = current.get(keys[item.capability_id])
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
    with Session(engine, expire_on_commit=False) as session:
        scope = drafting_scope(session, org_id)
        ids = [
            e.id
            for e in active_employees(session, org_id)
            if e.id not in scope.excluded_employee_ids
            and (employee_ids is None or str(e.id) in employee_ids)
        ]
        setting = read_setting(session, org_id)
        if setting:
            setting.paused_until, setting.pause_reason = None, None
            session.add(setting)
            session.commit()
            # Release the setting row before ChatGPT opens its own locking session.
            session.expunge(setting)
    capability_error = None
    if setting and setting.enabled and setting.kind != "chatgpt_subscription":
        try:
            validate_capabilities(
                setting.provider, setting.model, setting.reasoning_effort
            )
        except ModelUnavailable as error:
            capability_error = error
    counts = {"generated": 0, "failed": 0, "handed_over": 0}
    if employee_ids is not None and not ids:
        return
    for index, employee_id in enumerate(ids):
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
                current_scope = drafting_scope(session, org_id)
                capabilities = [
                    c
                    for c in list_capabilities(session, org_id, schema.id)
                    if c.key not in current_scope.excluded_capability_keys
                ]
                groups = task_groups(session, org_id)
                records = collect(session, org_id, employee, groups)
                setting = read_setting(session, org_id)
                if setting is None or not setting.enabled:
                    raise ModelDisabled()
                if capability_error:
                    raise capability_error
                items, generated_by = model.draft(
                    records, capabilities, model_config(setting)
                )
                items = postprocess(
                    session, org_id, employee, items, capabilities, groups
                )
                generated_by["schema_version_id"] = str(schema.id)
                written = write_initial_request(
                    session, org_id, employee, items, generated_by
                )
                progress_row(session, org_id, employee_id, InferenceStatus.GENERATED)
                session.commit()
                counts["generated" if written else "handed_over"] += 1
            except ModelRateLimited as error:
                session.rollback()
                pause_drafting(org_id, ids[index:], error)
                break
            except Exception as error:
                # Never expose record contents or database error parameters in progress.
                session.rollback()
                message = "起草失败，请重试；若仍失败请联系管理员"
                if isinstance(error, (InvalidDraft, ModelDisabled)):
                    message = str(error)
                elif isinstance(error, SecretUnavailable):
                    message = "保存的模型密钥已失效，请重新填写"
                elif isinstance(error, ModelOutputInvalid):
                    message = "模型输出不符合格式，已重试 3 次"
                elif isinstance(error, ModelServiceError):
                    message = str(error)
                elif isinstance(error, ModelUnavailable):
                    message = (
                        str(error)
                        if "ChatGPT 登录" in str(error)
                        else f"连不上模型服务：{error}"
                    )
                progress_row(
                    session, org_id, employee_id, InferenceStatus.FAILED, message
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
            details={"mode": "model", **counts},
        )
        session.commit()


def pause_drafting(
    org_id: UUID, ids: list[UUID], error: ModelRateLimited, *, incremental: bool = False
) -> None:
    retry_at = error.retry_at or utc_now() + timedelta(minutes=30)
    with Session(engine) as session:
        setting = read_setting(session, org_id)
        assert setting is not None
        setting.paused_until, setting.pause_reason = retry_at, error.reason
        session.add(setting)
        for employee_id in ids:
            progress_row(
                session,
                org_id,
                employee_id,
                InferenceStatus.PENDING,
                f"模型用量已到上限（{error.reason}），预计 {retry_at.isoformat()} 自动继续",
            )
        session.flush()
        # The running Worker's async connector cannot use this sync transaction.
        App(connector=SyncPsycopgConnector()).configure_task(
            "profiles.draft_incremental" if incremental else "profiles.draft_initial",
            connection=session.connection().connection.driver_connection,
            lock=f"initial-profiles:{org_id}",
            queueing_lock=f"draft:{org_id}",
            schedule_at=retry_at,
        ).defer(org_id=str(org_id), employee_ids=[str(id_) for id_ in ids])
        session.commit()


def drafting_jobs(
    session: Session, org_id: UUID | None = None, *, queued_only: bool = False
) -> bool:
    return bool(
        session.connection()
        .execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM procrastinate_jobs "
                "WHERE task_name IN ('profiles.draft_initial', 'profiles.draft_incremental') AND status IN ('todo', 'doing') "
                "AND (CAST(:org_id AS text) IS NULL OR args->>'org_id' = :org_id) "
                "AND (NOT :queued_only OR status = 'todo'))"
            ),
            {"org_id": str(org_id) if org_id else None, "queued_only": queued_only},
        )
        .scalar_one()
    )


def queue_drafting(
    session: Session,
    org_id: UUID,
    scope: Literal["all", "failed"],
    *,
    pending_owner_only: bool = False,
) -> int:
    from app.worker.tasks import draft_initial

    session.exec(
        select(Organization).where(Organization.id == org_id).with_for_update()
    ).one()
    setting = read_setting(session, org_id)
    if setting is None or not setting.enabled:
        raise ModelRequired()
    try:
        get_schema(session, org_id)
    except SchemaNotFound:
        raise HTTPException(422, "请先启用能力词典") from None
    # Opting out after queueing preserves pending progress after its job finishes.
    if drafting_jobs(session, org_id):
        raise HTTPException(409, "已有起草任务正在排队或执行")
    failed = set(
        session.exec(
            select(ProfileProgress.employee_id).where(
                ProfileProgress.org_id == org_id,
                ProfileProgress.inference_status == InferenceStatus.FAILED,
            )
        )
    )
    excluded = drafting_scope(session, org_id).excluded_employee_ids
    pending = (
        set(
            session.exec(
                select(ProfileChangeRequest.subject_employee_id).where(
                    ProfileChangeRequest.org_id == org_id,
                    ProfileChangeRequest.origin == RequestOrigin.INITIAL_SYNC,
                    ProfileChangeRequest.status == RequestStatus.PENDING_OWNER,
                )
            ).all()
        )
        if pending_owner_only
        else None
    )
    ids = [
        e.id
        for e in active_employees(session, org_id)
        if e.id not in excluded
        and (scope == "all" or e.id in failed)
        and (pending is None or e.id in pending)
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
    mode: Literal["none", "model"]
    counts: dict[str, int]
    failed: list[FailedEmployee]
    requests: dict[str, int]
    paused_until: datetime | None = None
    pause_reason: str | None = None


def read_progress(session: Session, org_id: UUID) -> DraftProgress:
    scope = drafting_scope(session, org_id)
    active = active_employees(session, org_id)
    employees = {e.id: e for e in active if e.id not in scope.excluded_employee_ids}
    rows = list(
        session.exec(
            select(ProfileProgress).where(
                ProfileProgress.org_id == org_id,
                col(ProfileProgress.employee_id).in_(employees),
            )
        )
    )
    counts = {
        "excluded": len(active) - len(employees),
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
        mode="model" if setting and setting.enabled else "none",
        paused_until=setting.paused_until if setting else None,
        pause_reason=setting.pause_reason if setting else None,
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
