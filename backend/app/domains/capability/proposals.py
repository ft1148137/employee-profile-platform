"""Store capability suggestions without changing the dictionary or profiles."""

import logging
from collections import defaultdict
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func
from sqlmodel import Session, col, select

from app.authz.deps import get_org_admin
from app.authz.principal import Principal
from app.domains.audit.service import record_audit
from app.domains.base import utc_now
from app.domains.capability.models import (
    CapabilityDefinition,
    CapabilityProposal,
    CapabilityProposalEvidence,
    CapabilitySchemaVersion,
    ProposalStatus,
    SchemaStatus,
)
from app.domains.capability.schemas import (
    CapabilityProposalPublic,
    CapabilityProposalsPublic,
    CapabilitySuggestionResult,
    SuggestCapability,
)
from app.domains.evidence.models import (
    Evidence,
    EvidenceKind,
    EvidenceStatus,
    Visibility,
)
from app.domains.org.models import Employee, EmploymentStatus, Organization

if TYPE_CHECKING:
    from app.domains.drafting.model import DraftProposal

logger = logging.getLogger(__name__)


def name_key(name: str) -> str:
    return "".join(name.casefold().split())


class SuggestionEmployeeRequired(HTTPException):
    def __init__(self) -> None:
        super().__init__(403, "只有在职员工可以建议新增能力")


class SuggestionLimitReached(HTTPException):
    def __init__(self) -> None:
        super().__init__(429, "你已有 5 条待处理的能力建议，请等管理员处理后再提交")


def suggest_capability(
    principal: Principal,
    session: Session,
    name: str,
    description: str,
    reason: str,
    *,
    commit: bool = True,
) -> CapabilitySuggestionResult:
    employee = session.exec(
        select(Employee).where(
            Employee.org_id == principal.org_id,
            Employee.id == principal.employee_id,
            Employee.employment_status == EmploymentStatus.ACTIVE,
        )
    ).first()
    if employee is None:
        raise SuggestionEmployeeRequired()
    body = SuggestCapability(name=name, description=description, reason=reason)
    # Use the same lock as drafting, dictionary publication and proposal decisions.
    session.exec(
        select(Organization.id)
        .where(Organization.id == principal.org_id)
        .with_for_update()
    ).one()
    key = name_key(body.name)
    names = session.exec(
        select(CapabilityDefinition.name)
        .join(
            CapabilitySchemaVersion,
            (col(CapabilitySchemaVersion.org_id) == CapabilityDefinition.org_id)
            & (CapabilitySchemaVersion.id == CapabilityDefinition.schema_version_id),
        )
        .where(
            CapabilityDefinition.org_id == principal.org_id,
            CapabilitySchemaVersion.status == SchemaStatus.ACTIVE,
        )
    ).all()
    if existing := next((n for n in names if name_key(n) == key), None):
        return CapabilitySuggestionResult(message=f"词典里已经有『{existing}』")
    previous = session.exec(
        select(CapabilityProposal)
        .where(
            CapabilityProposal.org_id == principal.org_id,
            CapabilityProposal.name_key == key,
        )
        .order_by(col(CapabilityProposal.updated_at).desc())
    ).first()
    if previous and previous.status != ProposalStatus.OPEN:
        decision = (
            "已加入词典"
            if previous.status == ProposalStatus.ACCEPTED
            else "决定暂不收录"
        )
        return CapabilitySuggestionResult(message=f"管理员已经处理过这项：{decision}")
    if previous is None:
        pending = session.exec(
            select(func.count())
            .select_from(CapabilityProposal)
            .where(
                CapabilityProposal.org_id == principal.org_id,
                CapabilityProposal.proposed_by_employee_id == employee.id,
                CapabilityProposal.status == ProposalStatus.OPEN,
            )
        ).one()
        if pending >= 5:
            raise SuggestionLimitReached()
        row = CapabilityProposal(
            org_id=principal.org_id,
            proposed_by_employee_id=employee.id,
            name_key=key,
            **body.model_dump(),
        )
        session.add(row)
        session.flush()
        record_audit(
            session,
            org_id=principal.org_id,
            actor_type=principal.actor_type,
            actor_id=str(principal.user_id),
            action="capability_proposal.suggested",
            target_type="capability_proposal",
            target_id=str(row.id),
            details={},
        )
        if commit:
            session.commit()
    return CapabilitySuggestionResult(
        message="已提交给企业管理员，加进词典后就可以在这里添加"
    )


def store_proposals(
    session: Session, org_id: UUID, employee_id: UUID, proposals: list[DraftProposal]
) -> None:
    if not proposals:
        return
    try:
        with session.begin_nested():
            # Serialize same-name creation and terminal-state checks within the tenant.
            session.exec(
                select(Organization).where(Organization.id == org_id).with_for_update()
            ).one()
            for candidate in proposals:
                ids = set(
                    session.exec(
                        select(Evidence.id).where(
                            Evidence.org_id == org_id,
                            Evidence.subject_employee_id == employee_id,
                            col(Evidence.id).in_(candidate.evidence_ids),
                            Evidence.kind == EvidenceKind.WORK_RECORD,
                            Evidence.visibility == Visibility.STANDARD,
                            Evidence.status == EvidenceStatus.ACTIVE,
                        )
                    ).all()
                )
                if len(ids) < 2:
                    continue
                key = name_key(candidate.proposal.name)
                rows = session.exec(
                    select(CapabilityProposal).where(
                        CapabilityProposal.org_id == org_id,
                        CapabilityProposal.name_key == key,
                    )
                ).all()
                if any(row.status != ProposalStatus.OPEN for row in rows):
                    continue
                row = (
                    rows[0]
                    if rows
                    else CapabilityProposal(
                        org_id=org_id,
                        name_key=key,
                        **candidate.proposal.model_dump(exclude={"evidence"}),
                    )
                )
                row.updated_at = utc_now()
                session.add(row)
                session.flush()
                existing = set(
                    session.exec(
                        select(CapabilityProposalEvidence.evidence_id).where(
                            CapabilityProposalEvidence.org_id == org_id,
                            CapabilityProposalEvidence.proposal_id == row.id,
                        )
                    ).all()
                )
                for id_ in sorted(ids - existing)[: max(0, 10 - len(existing))]:
                    session.add(
                        CapabilityProposalEvidence(
                            org_id=org_id, proposal_id=row.id, evidence_id=id_
                        )
                    )
                session.flush()
    except Exception as error:
        logger.warning(
            "Capability proposal write failed org=%s employee=%s count=%s error_type=%s",
            org_id,
            employee_id,
            len(proposals),
            type(error).__name__,
        )


def list_proposals(session: Session, principal: Principal) -> CapabilityProposalsPublic:
    get_org_admin(principal)
    proposals = session.exec(
        select(CapabilityProposal)
        .where(
            CapabilityProposal.org_id == principal.org_id,
            CapabilityProposal.status == ProposalStatus.OPEN,
        )
        .order_by(
            col(CapabilityProposal.updated_at).desc(), col(CapabilityProposal.id).desc()
        )
    ).all()
    refs = (
        session.exec(
            select(
                CapabilityProposalEvidence.proposal_id,
                Evidence.title,
                Evidence.subject_employee_id,
            )
            .join(
                Evidence,
                (col(Evidence.org_id) == CapabilityProposalEvidence.org_id)
                & (Evidence.id == CapabilityProposalEvidence.evidence_id),
            )
            .where(
                CapabilityProposalEvidence.org_id == principal.org_id,
                col(CapabilityProposalEvidence.proposal_id).in_(
                    [p.id for p in proposals]
                ),
            )
            .order_by(col(CapabilityProposalEvidence.evidence_id))
        ).all()
        if proposals
        else []
    )
    by_proposal: dict[UUID, list[tuple[str, UUID | None]]] = defaultdict(list)
    for id_, title, employee in refs:
        by_proposal[id_].append((title, employee))
    names = (
        dict(
            session.exec(
                select(Employee.id, Employee.display_name).where(
                    Employee.org_id == principal.org_id,
                    col(Employee.id).in_(
                        [p.proposed_by_employee_id for p in proposals]
                    ),
                )
            ).all()
        )
        if proposals
        else {}
    )
    return CapabilityProposalsPublic(
        data=[
            CapabilityProposalPublic(
                **row.model_dump(),
                proposed_by_employee_name=(
                    names.get(row.proposed_by_employee_id)
                    if row.proposed_by_employee_id
                    else None
                ),
                evidence_count=len(
                    titles := [title for title, _ in by_proposal[row.id]]
                ),
                evidence_titles=titles,
                employee_count=len(
                    {
                        employee
                        for _, employee in by_proposal[row.id]
                        if employee is not None
                    }
                ),
            )
            for row in proposals
        ]
    )


class ProposalConflict(HTTPException):
    def __init__(self, message: str = "建议已处理，请刷新列表") -> None:
        super().__init__(409, message)


class ProposalNotFound(HTTPException):
    def __init__(self) -> None:
        super().__init__(404, "找不到这条建议")


def get_proposal(
    session: Session, principal: Principal, id_: UUID
) -> CapabilityProposal:
    get_org_admin(principal)
    row = session.exec(
        select(CapabilityProposal)
        .where(
            CapabilityProposal.org_id == principal.org_id, CapabilityProposal.id == id_
        )
        .execution_options(populate_existing=True)
    ).one_or_none()
    if row is None:
        raise ProposalNotFound()
    return row


def require_restorable(
    session: Session, principal: Principal, row: CapabilityProposal
) -> None:
    if row.status != ProposalStatus.DISMISSED:
        raise ProposalConflict("建议已不再是忽略状态，不能撤销")
    if session.exec(
        select(CapabilityProposal.id).where(
            CapabilityProposal.org_id == principal.org_id,
            CapabilityProposal.name_key == row.name_key,
            CapabilityProposal.status == ProposalStatus.OPEN,
        )
    ).first():
        raise ProposalConflict("同名已有新的待处理建议，不能撤销")


def decide_proposal(
    session: Session,
    principal: Principal,
    row: CapabilityProposal,
    status: ProposalStatus,
) -> None:
    row.status = status
    row.updated_at = utc_now()
    row.decided_by_user_id = (
        principal.user_id if status != ProposalStatus.OPEN else None
    )
    row.decided_at = row.updated_at if status != ProposalStatus.OPEN else None
    session.add(row)
    record_audit(
        session,
        org_id=principal.org_id,
        actor_type=principal.actor_type,
        actor_id=str(principal.user_id),
        action=f"capability_proposal.{status.value}",
        target_type="capability_proposal",
        target_id=str(row.id),
        details={},
    )


def dismiss_proposal(
    session: Session,
    principal: Principal,
    id_: UUID,
    *,
    restore: bool = False,
    commit: bool = True,
) -> None:
    get_org_admin(principal)
    # Share the drafting and dictionary lock, including uniqueness checks on undo.
    session.exec(
        select(Organization.id)
        .where(Organization.id == principal.org_id)
        .with_for_update()
    ).one()
    row = get_proposal(session, principal, id_)
    if restore:
        require_restorable(session, principal, row)
    elif row.status != ProposalStatus.OPEN:
        raise ProposalConflict()
    decide_proposal(
        session,
        principal,
        row,
        ProposalStatus.OPEN if restore else ProposalStatus.DISMISSED,
    )
    if commit:
        session.commit()


def accept_proposals(session: Session, principal: Principal, names: list[str]) -> None:
    get_org_admin(principal)
    for row in session.exec(
        select(CapabilityProposal).where(
            CapabilityProposal.org_id == principal.org_id,
            CapabilityProposal.status == ProposalStatus.OPEN,
            col(CapabilityProposal.name_key).in_([name_key(name) for name in names]),
        )
    ).all():
        decide_proposal(session, principal, row, ProposalStatus.ACCEPTED)
