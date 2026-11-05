import hashlib
import json
from datetime import datetime
from uuid import UUID

from fastapi import HTTPException
from pydantic import JsonValue
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import aliased
from sqlmodel import Session, col, func, select

from app.authz.policies import can_view_evidence_content, employee_scope_clause
from app.authz.principal import Principal
from app.domains.audit.models import ActorKind
from app.domains.audit.service import record_audit
from app.domains.capability.models import CapabilityDefinition
from app.domains.evidence.models import (
    Evidence,
    EvidenceKind,
    EvidenceStatus,
    Visibility,
)
from app.domains.evidence.schemas import (
    EvidenceClaimPublic,
    EvidenceDetail,
    EvidenceList,
    EvidenceSummary,
    TaskPublic,
    WorkEventPublic,
)
from app.domains.org.models import Employee
from app.domains.org.service import visible_employee
from app.domains.profile.models import ClaimEvidence, EvidenceRelation, ProfileClaim
from app.domains.work.models import Task, WorkEvent


class EvidenceNotFound(HTTPException):
    def __init__(self) -> None:
        super().__init__(404, "证据不存在或你没有权限查看")


def restricted_title(kind: EvidenceKind) -> str:
    return {
        EvidenceKind.SELF_REPORT: "员工自述（受限）",
        EvidenceKind.MANAGER_REVIEW: "经理评价（受限）",
        EvidenceKind.WORK_RECORD: "工作记录（受限）",
    }[kind]


def evidence_access(
    session: Session,
    principal: Principal,
    ids: set[UUID],
    *,
    review_permissions: dict[UUID, bool] | None = None,
) -> dict[UUID, tuple[Evidence, bool]]:
    from app.authz.review import review_access
    from app.domains.change_request.models import (
        ChangeItem,
        ChangeItemEvidence,
        ProfileChangeRequest,
        RequestStatus,
    )

    # Only reuse permissions computed by review_access for this principal and call.
    permissions = review_permissions if review_permissions is not None else {}
    access: dict[UUID, tuple[Evidence, bool]] = {}
    rows = session.exec(
        select(Evidence, Employee, ProfileChangeRequest)
        .join(
            Employee,
            (col(Employee.id) == Evidence.subject_employee_id)
            & (col(Employee.org_id) == Evidence.org_id),
        )
        .outerjoin(
            ChangeItemEvidence,
            (col(ChangeItemEvidence.evidence_id) == Evidence.id)
            & (col(ChangeItemEvidence.org_id) == Evidence.org_id),
        )
        .outerjoin(
            ChangeItem,
            (col(ChangeItem.id) == ChangeItemEvidence.item_id)
            & (col(ChangeItem.org_id) == ChangeItemEvidence.org_id),
        )
        .outerjoin(
            ProfileChangeRequest,
            (col(ProfileChangeRequest.id) == ChangeItem.request_id)
            & (col(ProfileChangeRequest.org_id) == ChangeItem.org_id)
            & (col(ProfileChangeRequest.status) == RequestStatus.PENDING_REVIEW),
        )
        .where(
            Evidence.org_id == principal.org_id,
            col(Evidence.id).in_(ids),
            employee_scope_clause(principal),
        )
    ).all()
    # Joined rows may list another request first for the same evidence.
    for e, _, request in rows:
        if request is not None and permissions.get(request.id, False):
            access[e.id] = (e, True)
    for e, employee, request in rows:
        readable = access.get(e.id, (e, False))[1] or can_view_evidence_content(
            principal, subject_employee=employee, visibility=e.visibility
        )
        if not readable and request is not None:
            if request.id not in permissions:
                permissions[request.id] = review_access(session, principal, request)
            readable = permissions[request.id]
        access[e.id] = (e, readable)
    return access


def evidence_summary(evidence: Evidence, readable: bool) -> EvidenceSummary:
    return EvidenceSummary.model_validate(
        evidence,
        update={
            "redacted": not readable,
            "title": evidence.title if readable else restricted_title(evidence.kind),
        },
    )


def list_evidence(
    session: Session,
    principal: Principal,
    employee_id: UUID,
    *,
    kind: EvidenceKind | None,
    include_inactive: bool,
    skip: int,
    limit: int,
) -> EvidenceList:
    employee = visible_employee(session, principal, employee_id)
    query = select(Evidence).where(
        Evidence.org_id == principal.org_id, Evidence.subject_employee_id == employee.id
    )
    if kind is not None:
        query = query.where(Evidence.kind == kind)
    if not include_inactive:
        query = query.where(Evidence.status == EvidenceStatus.ACTIVE)
    count = session.exec(select(func.count()).select_from(query.subquery())).one()
    rows = session.exec(
        query.order_by(col(Evidence.occurred_at).desc().nulls_last(), col(Evidence.id))
        .offset(skip)
        .limit(limit)
    ).all()
    return EvidenceList(
        data=[
            evidence_summary(
                e,
                can_view_evidence_content(
                    principal, subject_employee=employee, visibility=e.visibility
                ),
            )
            for e in rows
        ],
        count=count,
    )


def read_evidence(
    session: Session, principal: Principal, evidence_id: UUID
) -> EvidenceDetail:
    access = evidence_access(session, principal, {evidence_id})
    if evidence_id not in access:
        raise EvidenceNotFound()
    return evidence_details(session, principal, access)[evidence_id]


def evidence_details(
    session: Session,
    principal: Principal,
    access: dict[UUID, tuple[Evidence, bool]],
) -> dict[UUID, EvidenceDetail]:
    """Load details in batches after evidence_access has checked visibility."""
    events = {
        event.id: event
        for event in session.exec(
            select(WorkEvent).where(
                WorkEvent.org_id == principal.org_id,
                col(WorkEvent.id).in_(
                    {
                        e.event_id
                        for e, readable in access.values()
                        if readable and e.event_id
                    }
                ),
            )
        ).all()
    }
    tasks = {
        task.id: task
        for task in session.exec(
            select(Task).where(
                Task.org_id == principal.org_id,
                col(Task.id).in_(
                    {event.task_id for event in events.values() if event.task_id}
                ),
            )
        ).all()
    }
    successor = aliased(ProfileClaim)
    claims = session.exec(
        select(
            ClaimEvidence.evidence_id,
            ProfileClaim.id,
            CapabilityDefinition.name,
            ClaimEvidence.relation,
        )
        .join(
            ClaimEvidence,
            (col(ClaimEvidence.claim_id) == ProfileClaim.id)
            & (col(ClaimEvidence.org_id) == ProfileClaim.org_id),
        )
        .join(
            CapabilityDefinition,
            (col(CapabilityDefinition.id) == ProfileClaim.capability_definition_id)
            & (col(CapabilityDefinition.org_id) == ProfileClaim.org_id),
        )
        .join(
            Employee,
            (col(Employee.id) == ProfileClaim.subject_employee_id)
            & (col(Employee.org_id) == ProfileClaim.org_id),
        )
        .where(
            ProfileClaim.org_id == principal.org_id,
            col(ClaimEvidence.evidence_id).in_(access),
            employee_scope_clause(principal),
            ~select(successor.id)
            .where(
                successor.org_id == principal.org_id,
                successor.supersedes_claim_id == ProfileClaim.id,
            )
            .exists(),
        )
        .order_by(col(ProfileClaim.id))
    ).all()
    claim_details: dict[UUID, list[EvidenceClaimPublic]] = {}
    for evidence_id, id_, name, relation in claims:
        claim_details.setdefault(evidence_id, []).append(
            EvidenceClaimPublic(
                claim_id=id_, capability_name=name, relation=EvidenceRelation(relation)
            )
        )
    result = {}
    for evidence_id, (evidence, readable) in access.items():
        event = (
            events.get(evidence.event_id) if readable and evidence.event_id else None
        )
        task = tasks.get(event.task_id) if event and event.task_id else None
        result[evidence_id] = EvidenceDetail(
            **evidence_summary(evidence, readable).model_dump(),
            content=evidence.content if readable else None,
            provenance=evidence.provenance
            if readable
            else {
                "source": evidence.source,
                "imported_at": evidence.provenance.get(
                    "imported_at", evidence.created_at.isoformat()
                ),
            },
            work_event=WorkEventPublic.model_validate(event) if event else None,
            task=TaskPublic.model_validate(task) if task else None,
            supersedes_evidence_id=evidence.supersedes_evidence_id,
            claims=claim_details.get(evidence_id, []),
        )
    return result


def append_evidence(
    session: Session,
    *,
    org_id: UUID,
    subject_employee_id: UUID,
    kind: EvidenceKind,
    source: str,
    source_ref: str,
    title: str,
    content: dict[str, JsonValue],
    visibility: Visibility,
    provenance: dict[str, JsonValue],
    occurred_at: datetime | None = None,
    event_id: UUID | None = None,
) -> tuple[Evidence, bool]:
    """Append idempotently, including concurrent writers; leave commit to the caller."""
    evidence = Evidence(
        org_id=org_id,
        subject_employee_id=subject_employee_id,
        kind=kind,
        source=source,
        source_ref=source_ref,
        title=title,
        content=content,
        visibility=visibility,
        provenance=provenance,
        occurred_at=occurred_at,
        event_id=event_id,
        content_hash=_content_hash(content),
    )
    return _append_evidence(session, evidence)


def _content_hash(content: dict[str, JsonValue]) -> str:
    canonical = json.dumps(
        content,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _append_evidence(session: Session, evidence: Evidence) -> tuple[Evidence, bool]:
    statement = (
        insert(Evidence)
        .values(**evidence.model_dump())
        .on_conflict_do_nothing(constraint="uq_evidence_content")
        .returning(Evidence)
    )
    inserted = session.scalars(statement).one_or_none()
    if inserted is not None:
        return inserted, True
    existing = session.exec(
        select(Evidence).where(
            Evidence.org_id == evidence.org_id,
            Evidence.source == evidence.source,
            Evidence.source_ref == evidence.source_ref,
            Evidence.subject_employee_id == evidence.subject_employee_id,
            Evidence.content_hash == evidence.content_hash,
        )
    ).one()
    return existing, False


class InactiveEvidence(ValueError):
    """Only active evidence may be superseded or retracted."""


class DuplicateEvidenceContent(ValueError):
    """A correction must not reuse content from an existing evidence row."""


def _lock_active_evidence(session: Session, evidence: Evidence) -> Evidence:
    # Refresh cached instances after a concurrent writer releases the row lock.
    locked = session.exec(
        select(Evidence)
        .where(Evidence.org_id == evidence.org_id, Evidence.id == evidence.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    if locked.status != EvidenceStatus.ACTIVE:
        raise InactiveEvidence("Evidence is no longer active")
    return locked


def supersede_evidence(
    session: Session,
    *,
    original: Evidence,
    content: dict[str, JsonValue],
    title: str,
    occurred_at: datetime | None,
    event_id: UUID | None,
    provenance: dict[str, JsonValue],
    reason: str,
    actor_type: ActorKind,
    actor_id: str | None,
) -> Evidence:
    """Append a correction and audit in the caller's transaction."""
    original = _lock_active_evidence(session, original)
    content_hash = _content_hash(content)
    if content_hash == original.content_hash:
        return original
    correction, created = _append_evidence(
        session,
        Evidence(
            org_id=original.org_id,
            subject_employee_id=original.subject_employee_id,
            kind=original.kind,
            source=original.source,
            source_ref=original.source_ref,
            visibility=original.visibility,
            supersedes_evidence_id=original.id,
            content=content,
            content_hash=content_hash,
            title=title,
            occurred_at=occurred_at,
            event_id=event_id,
            provenance=provenance,
        ),
    )
    if not created:
        raise DuplicateEvidenceContent("Correction content already exists")
    original.status = EvidenceStatus.SUPERSEDED
    original.status_reason = reason
    record_audit(
        session,
        org_id=original.org_id,
        actor_type=actor_type,
        actor_id=actor_id,
        action="evidence.superseded",
        target_type="evidence",
        target_id=str(original.id),
        details={
            "original_evidence_id": str(original.id),
            "new_evidence_id": str(correction.id),
            "reason": reason,
            "source": original.source,
            "source_ref": original.source_ref,
        },
    )
    return correction


def retract_evidence(
    session: Session,
    *,
    evidence: Evidence,
    reason: str,
    actor_type: ActorKind,
    actor_id: str | None,
) -> None:
    """Retract active evidence and audit in the caller's transaction."""
    evidence = _lock_active_evidence(session, evidence)
    evidence.status = EvidenceStatus.RETRACTED
    evidence.status_reason = reason
    record_audit(
        session,
        org_id=evidence.org_id,
        actor_type=actor_type,
        actor_id=actor_id,
        action="evidence.retracted",
        target_type="evidence",
        target_id=str(evidence.id),
        details={
            "original_evidence_id": str(evidence.id),
            "reason": reason,
            "source": evidence.source,
            "source_ref": evidence.source_ref,
        },
    )
