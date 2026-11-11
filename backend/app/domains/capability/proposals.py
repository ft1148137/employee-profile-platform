"""Store drafting suggestions without changing the dictionary or profiles."""

import logging
from typing import TYPE_CHECKING
from uuid import UUID

from sqlmodel import Session, col, select

from app.domains.base import utc_now
from app.domains.capability.models import (
    CapabilityProposal,
    CapabilityProposalEvidence,
    ProposalStatus,
)
from app.domains.evidence.models import (
    Evidence,
    EvidenceKind,
    EvidenceStatus,
    Visibility,
)
from app.domains.org.models import Organization

if TYPE_CHECKING:
    from app.domains.drafting.model import DraftProposal

logger = logging.getLogger(__name__)


def name_key(name: str) -> str:
    return "".join(name.casefold().split())


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
