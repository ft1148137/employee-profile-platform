from datetime import UTC, datetime
from uuid import UUID

from procrastinate import App, SyncPsycopgConnector
from sqlmodel import Session, col, delete, select

from app.authz.principal import Principal
from app.core.db import engine
from app.domains.audit.service import record_audit
from app.domains.base import utc_now
from app.domains.capability.proposals import store_proposals
from app.domains.capability.service import get_schema, list_capabilities
from app.domains.change_request.models import (
    ChangeItem,
    ChangeItemEvidence,
    ChangeOp,
    ItemDecision,
    ProfileChangeRequest,
    RequestOrigin,
    RequestStatus,
    ReviewDecision,
    ReviewVia,
)
from app.domains.drafting import model
from app.domains.drafting.collect import collect, task_groups
from app.domains.drafting.model import DraftItem
from app.domains.drafting.service import (
    InvalidDraft,
    ModelDisabled,
    active_employees,
    pause_drafting,
    postprocess,
    progress_row,
)
from app.domains.drafting.settings import drafting_scope, read_settings
from app.domains.evidence.models import Evidence, Visibility
from app.domains.inbox.models import InboxKind
from app.domains.inbox.service import notify
from app.domains.model.client import ModelRateLimited
from app.domains.model.service import model_config, read_setting
from app.domains.org.models import Employee, EmploymentStatus, Organization
from app.domains.profile.models import (
    AssessmentStatus,
    ClaimEvidence,
    InferenceStatus,
    ProfileClaim,
    ProfileProgress,
    ProfileVersion,
)
from app.domains.review.publish import (
    current_claims,
    effective_evidence,
    jira_only,
    publish,
)

OPEN = (RequestStatus.DRAFT, RequestStatus.PENDING_OWNER, RequestStatus.PENDING_REVIEW)


def latest_version(
    session: Session, org_id: UUID, employee_id: UUID
) -> ProfileVersion | None:
    return session.exec(
        select(ProfileVersion)
        .where(
            ProfileVersion.org_id == org_id,
            ProfileVersion.subject_employee_id == employee_id,
        )
        .order_by(col(ProfileVersion.revision).desc())
    ).first()


def pending_requests(
    session: Session, org_id: UUID, employee_id: UUID, *, lock: bool = False
) -> list[ProfileChangeRequest]:
    query = (
        select(ProfileChangeRequest)
        .where(
            ProfileChangeRequest.org_id == org_id,
            ProfileChangeRequest.subject_employee_id == employee_id,
            col(ProfileChangeRequest.origin).in_(
                (RequestOrigin.INITIAL_SYNC, RequestOrigin.INCREMENTAL_SYNC)
            ),
            col(ProfileChangeRequest.status).in_(OPEN),
        )
        .order_by(col(ProfileChangeRequest.id))
    )
    if lock:
        query = query.with_for_update()
    return list(session.exec(query.execution_options(populate_existing=True)))


def write_items(
    session: Session,
    request: ProfileChangeRequest,
    items: list[DraftItem],
    current: dict[UUID, ProfileClaim],
) -> list[ChangeItem]:
    rows = []
    for position, item in enumerate(items):
        target = current.get(item.capability_id)
        row = ChangeItem(
            org_id=request.org_id,
            request_id=request.id,
            position=position,
            op=item.op,
            capability_definition_id=item.capability_id,
            target_claim_id=target.id if target else None,
            after=item.after.model_dump(mode="json"),
            explanation=item.explanation,
            uncertainty=item.uncertainty,
        )
        session.add(row)
        session.flush()
        session.add_all(
            [
                ChangeItemEvidence(
                    org_id=request.org_id,
                    item_id=row.id,
                    evidence_id=id_,
                    relation=relation,
                )
                for id_, relation in item.evidence.items()
            ]
        )
        rows.append(row)
    session.flush()
    return rows


def draft_employee(session: Session, org_id: UUID, employee_id: UUID) -> None:
    if not read_settings(session, org_id).enabled:
        return
    employee = session.exec(
        select(Employee).where(Employee.org_id == org_id, Employee.id == employee_id)
    ).one()
    scope = drafting_scope(session, org_id)
    if (
        employee.employment_status != EmploymentStatus.ACTIVE
        or employee.id in scope.excluded_employee_ids
    ):
        return
    version = latest_version(session, org_id, employee_id)
    requests = pending_requests(session, org_id, employee_id)
    if version is None or any(
        r.origin == RequestOrigin.INITIAL_SYNC
        or r.status != RequestStatus.PENDING_OWNER
        for r in requests
    ):
        return
    if len(requests) > 1:
        raise InvalidDraft("存在多个待处理增量请求，请先处理")
    pending = requests[0] if requests else None
    expected_requests = [(r.id, r.revision) for r in requests]
    progress = session.get(ProfileProgress, (org_id, employee_id))
    watermark = progress.incremental_drafted_at if progress else None
    current = current_claims(session, org_id, employee_id)
    links = list(
        session.exec(
            select(ClaimEvidence).where(
                ClaimEvidence.org_id == org_id,
                col(ClaimEvidence.claim_id).in_([c.id for c in current.values()]),
            )
        )
    )
    referenced = list(
        session.exec(
            select(Evidence.created_at).where(
                Evidence.org_id == org_id,
                col(Evidence.id).in_([link.evidence_id for link in links]),
                Evidence.source == "jira",
            )
        )
    )
    # Approval can happen after new work arrives; publication time would lose that work.
    lower = watermark or max(referenced, default=datetime.min.replace(tzinfo=UTC))
    groups = task_groups(session, org_id)
    records = [
        r
        for r in collect(session, org_id, employee, groups)
        if r.evidence.source == "jira" and r.evidence.visibility == Visibility.STANDARD
    ]
    fresh = [r for r in records if r.evidence.created_at > lower]
    if not fresh:
        return
    cutoff = max(r.evidence.created_at for r in fresh)
    # A replacement must retain the previous candidate's unreviewed records.
    if pending:
        lower = datetime.fromisoformat(str(pending.generated_by["incremental_from"]))
    records = [r for r in records if lower < r.evidence.created_at <= cutoff]
    schema = get_schema(session, org_id)
    capabilities = list_capabilities(session, org_id, schema.id)
    evidence_refs = {
        id_: f"E{i}"
        for i, id_ in enumerate(sorted({link.evidence_id for link in links}), 1)
    }
    setting = read_setting(session, org_id)
    if setting is None or not setting.enabled:
        raise ModelDisabled()
    if setting.paused_until and setting.paused_until > utc_now():
        raise ModelRateLimited(
            setting.paused_until, setting.pause_reason or "模型用量到上限"
        )
    result = model.draft(
        records,
        capabilities,
        model_config(setting),
        current=current,
        claim_evidence={
            key: [
                evidence_refs[link.evidence_id]
                for link in links
                if link.claim_id == claim.id
            ]
            for key, claim in current.items()
        },
    )
    items, generated_by = result.items, result.generated_by
    items = postprocess(session, org_id, employee, items, capabilities, groups)
    generated_by.update(
        schema_version_id=str(schema.id), incremental_from=lower.isoformat()
    )
    # Match the review service's lock order; never hold these locks over a model call.
    session.exec(
        select(Organization).where(Organization.id == org_id).with_for_update(read=True)
    ).one()
    locked = pending_requests(session, org_id, employee_id, lock=True)
    session.refresh(employee, with_for_update=True)
    latest = latest_version(session, org_id, employee_id)
    if progress:
        session.refresh(progress)
    session.refresh(setting)
    if not setting.enabled:
        raise ModelDisabled()
    if setting.paused_until and setting.paused_until <= utc_now():
        setting.paused_until, setting.pause_reason = None, None
        session.add(setting)
    current_scope = drafting_scope(session, org_id)
    if (
        latest is None
        or latest.id != version.id
        or expected_requests != [(r.id, r.revision) for r in locked]
        or get_schema(session, org_id).id != schema.id
        or (progress.incremental_drafted_at if progress else None) != watermark
        or employee.employment_status != EmploymentStatus.ACTIVE
        or employee.id in current_scope.excluded_employee_ids
        or not read_settings(session, org_id).enabled
    ):
        raise InvalidDraft("画像或起草设置已变化，请重新起草")
    current_by_id = {c.id: current[c.key] for c in capabilities if c.key in current}
    records_by_id = {r.evidence.id: r.evidence for r in records}
    completed = ProfileChangeRequest(
        org_id=org_id,
        subject_employee_id=employee_id,
        origin=RequestOrigin.INCREMENTAL_SYNC,
        status=RequestStatus.APPROVED,
        author={"actor_type": "system_worker"},
        base_version_id=version.id,
        generated_by=generated_by,
        decided_at=utc_now(),
    )
    automatic, review = [], []
    for item in items:
        target = current_by_id.get(item.capability_id)
        if item.op == ChangeOp.ATTACH_EVIDENCE and target:
            item.evidence = {
                id_: rel
                for id_, rel in item.evidence.items()
                if id_
                not in {
                    link.evidence_id for link in links if link.claim_id == target.id
                }
            }
            if not item.evidence:
                continue
            candidate = ChangeItem(
                org_id=org_id,
                request_id=completed.id,
                position=0,
                op=item.op,
                capability_definition_id=item.capability_id,
                target_claim_id=target.id,
            )
            merged = effective_evidence(
                session,
                candidate,
                target,
                {id_: (records_by_id[id_], rel) for id_, rel in item.evidence.items()},
            )
            if (
                not jira_only(merged)
                or target.assessment_status == AssessmentStatus.TENTATIVE
            ):
                automatic.append(item)
                continue
        review.append(item)
    if automatic:
        session.add(completed)
        session.flush()
        auto_items = write_items(session, completed, automatic, current_by_id)
        decision = ReviewDecision(
            org_id=org_id,
            request_id=completed.id,
            via=ReviewVia.SYNC,
            request_revision=completed.revision,
        )
        session.add(decision)
        session.flush()
        for row in auto_items:
            row.decision, row.review_decision_id = ItemDecision.APPROVED, decision.id
        version = publish(session, completed, auto_items, decision)
    if review or pending:
        if pending is None:
            pending = ProfileChangeRequest(
                org_id=org_id,
                subject_employee_id=employee_id,
                origin=RequestOrigin.INCREMENTAL_SYNC,
                status=RequestStatus.PENDING_OWNER,
                author={"actor_type": "system_worker"},
            )
            session.add(pending)
            session.flush()
            notify(
                session,
                pending,
                InboxKind.INITIAL_REVIEW,
                "增量画像已起草，请过目后提交",
            )
        else:
            pending.revision += 1
            old_items = select(ChangeItem.id).where(
                ChangeItem.org_id == org_id, ChangeItem.request_id == pending.id
            )
            session.exec(
                delete(ChangeItemEvidence).where(
                    col(ChangeItemEvidence.org_id) == org_id,
                    col(ChangeItemEvidence.item_id).in_(old_items),
                )
            )
            session.exec(
                delete(ChangeItem).where(
                    col(ChangeItem.org_id) == org_id,
                    col(ChangeItem.request_id) == pending.id,
                )
            )
        pending.base_version_id, pending.generated_by, pending.updated_at = (
            version.id,
            generated_by,
            utc_now(),
        )
        write_items(session, pending, review, current_by_id)
        if not review:
            pending.status = RequestStatus.WITHDRAWN
            notify(
                session,
                pending,
                InboxKind.STATUS_CHANGED,
                "增量请求已由新证据替代，无需继续处理",
            )
    progress = progress or ProfileProgress(org_id=org_id, employee_id=employee_id)
    progress.incremental_drafted_at = cutoff
    progress.inference_status, progress.inference_error, progress.updated_at = (
        InferenceStatus.GENERATED,
        None,
        utc_now(),
    )
    session.add(progress)
    record_audit(
        session,
        org_id=org_id,
        actor_type=Principal.system_worker(org_id).actor_type,
        actor_id=None,
        action="incremental_profiles.drafted",
        target_type="employee",
        target_id=str(employee_id),
        details={
            "published_version_id": str(version.id),
            "automatic_items": len(automatic),
            "review_items": len(review),
        },
    )
    store_proposals(session, org_id, employee_id, result.proposals)
    session.commit()


def run_incremental(
    principal: Principal, employee_ids: list[str] | None = None
) -> None:
    org_id = principal.org_id
    with Session(engine) as session:
        ids = [
            e.id
            for e in active_employees(session, org_id)
            if employee_ids is None or str(e.id) in employee_ids
        ]
    for index, employee_id in enumerate(ids):
        with Session(engine) as session:
            try:
                draft_employee(session, org_id, employee_id)
            except ModelRateLimited as error:
                session.rollback()
                pause_drafting(org_id, ids[index:], error, incremental=True)
                break
            except Exception:
                session.rollback()
                progress_row(
                    session,
                    org_id,
                    employee_id,
                    InferenceStatus.FAILED,
                    "增量起草失败，请重试；若仍失败请联系管理员",
                )
                session.commit()


def queue_incremental(session: Session, org_id: UUID) -> None:
    from app.domains.evidence.models import EvidenceKind

    if not read_settings(session, org_id).enabled:
        return
    # Include earlier manual sync records, even when this scheduled sync found no changes.
    ids = list(
        session.exec(
            select(Evidence.subject_employee_id)
            .where(
                Evidence.org_id == org_id,
                Evidence.kind == EvidenceKind.WORK_RECORD,
                Evidence.source == "jira",
            )
            .distinct()
        )
    )
    if ids:
        App(connector=SyncPsycopgConnector()).configure_task(
            "profiles.draft_incremental",
            connection=session.connection().connection.driver_connection,
            lock=f"initial-profiles:{org_id}",
        ).defer(org_id=str(org_id), employee_ids=[str(id_) for id_ in ids])
