from uuid import UUID

from fastapi import APIRouter, Query

from app.api.deps import SessionDep
from app.authz.deps import OrgPrincipalDep
from app.domains.evidence import service
from app.domains.evidence.models import EvidenceKind
from app.domains.evidence.schemas import EvidenceDetail, EvidenceList

router = APIRouter(prefix="/orgs/{org_id}", tags=["evidence"])


@router.get("/employees/{employee_id}/evidence", response_model=EvidenceList)
def read_employee_evidence(
    employee_id: UUID,
    session: SessionDep,
    principal: OrgPrincipalDep,
    kind: EvidenceKind | None = None,
    include_inactive: bool = False,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
) -> EvidenceList:
    return service.list_evidence(
        session,
        principal,
        employee_id,
        kind=kind,
        include_inactive=include_inactive,
        skip=skip,
        limit=limit,
    )


@router.get("/evidence/{evidence_id}", response_model=EvidenceDetail)
def read_evidence(
    evidence_id: UUID, session: SessionDep, principal: OrgPrincipalDep
) -> EvidenceDetail:
    return service.read_evidence(session, principal, evidence_id)
