from datetime import date, datetime
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, col, select

from app.authz.principal import Principal
from app.domains.audit.models import ActorKind
from app.domains.capability.models import CapabilityDefinition, DepartmentCapability
from app.domains.capability.service import get_schema, list_capabilities
from app.domains.capability.stats import CapabilityStatsForbidden, latest_profiles
from app.domains.change_request.models import ChangeItem
from app.domains.evidence.models import (
    ASSIGNMENT_FEEDBACK_SOURCE,
    Evidence,
    EvidenceStatus,
)
from app.domains.org.models import Department, Organization
from app.domains.org.service import managed_scope
from app.domains.profile.models import AssessmentStatus, ProfileVersion
from app.domains.profile.schemas import PublisherPublic, SnapshotClaim
from app.domains.profile.service import version_summaries
from app.domains.review.models import AppealStatus, ReviewAppeal

SearchStatus = Literal[
    AssessmentStatus.CONFIRMED, AssessmentStatus.TENTATIVE, AssessmentStatus.DISPUTED
]


class PeopleSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capability_key: str = Field(min_length=1, max_length=200)
    minimum_level: str | None = Field(default=None, max_length=200)
    include_ungraded: bool = False
    statuses: list[SearchStatus] = Field(default_factory=list)
    evidence_from: date | None = None
    evidence_to: date | None = None
    department_id: UUID | None = None
    department_capability_only: bool = False
    job_family: str | None = Field(default=None, max_length=50)
    work_location: str | None = Field(default=None, max_length=50)
    hire_date_from: date | None = None
    hire_date_to: date | None = None
    skip: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=100)


class PeopleSearchRow(BaseModel):
    employee_id: UUID
    display_name: str
    employee_code: str | None
    department_name: str | None
    capability_key: str
    capability_name: str
    statement: str
    level: str | None
    level_name: str | None
    status: SearchStatus
    evidence_count: int
    latest_evidence_at: datetime | None
    published_by: PublisherPublic
    published_at: datetime


class PeopleSearchResult(BaseModel):
    data: list[PeopleSearchRow]
    count: int


class PeopleSearchInvalid(HTTPException):
    def __init__(self, detail: str) -> None:
        super().__init__(422, detail)


def find_people(
    session: Session, principal: Principal, args: PeopleSearchInput
) -> PeopleSearchResult:
    if (
        principal.actor_type != ActorKind.SYSTEM_WORKER
        and not principal.is_org_admin
        and not principal.managed_department_ids
    ):
        raise CapabilityStatsForbidden()
    for start, end in (
        (args.evidence_from, args.evidence_to),
        (args.hire_date_from, args.hire_date_to),
    ):
        if start and end and start > end:
            raise PeopleSearchInvalid("起始日期不能晚于结束日期")
    schema = get_schema(session, principal.org_id)
    capability = next(
        (
            c
            for c in list_capabilities(session, principal.org_id, schema.id)
            if c.key == args.capability_key
        ),
        None,
    )
    if capability is None:
        raise PeopleSearchInvalid("能力不在当前启用的词典中")
    raw_levels = (capability.level_rubric or {}).get("levels", [])
    levels = (
        [v for v in raw_levels if isinstance(v, dict)]
        if isinstance(raw_levels, list)
        else []
    )
    ranks = {str(v["key"]): i for i, v in enumerate(levels)}
    labels = {str(v["key"]): str(v.get("label") or v["key"]) for v in levels}
    if args.minimum_level is not None and args.minimum_level not in ranks:
        raise PeopleSearchInvalid("最低等级不在当前能力的等级描述中")
    if (
        args.department_id
        and principal.actor_type != ActorKind.SYSTEM_WORKER
        and not principal.is_org_admin
        and args.department_id not in principal.managed_department_ids
    ):
        raise CapabilityStatsForbidden()
    department_ids = (
        managed_scope(session, principal.org_id, frozenset({args.department_id}))
        if args.department_id
        else None
    )
    configured = None
    if args.department_capability_only:
        assigned = session.exec(
            select(DepartmentCapability.department_id).where(
                DepartmentCapability.org_id == principal.org_id,
                DepartmentCapability.capability_key == args.capability_key,
            )
        ).all()
        configured = managed_scope(session, principal.org_id, frozenset(assigned))
    fields = session.exec(
        select(Organization.employee_fields).where(Organization.id == principal.org_id)
    ).one()
    definitions = {
        c.id: c
        for c in session.exec(
            select(CapabilityDefinition).where(
                CapabilityDefinition.org_id == principal.org_id,
                CapabilityDefinition.key == args.capability_key,
            )
        ).all()
    }
    departments = dict(
        session.exec(
            select(Department.id, Department.name).where(
                Department.org_id == principal.org_id
            )
        ).all()
    )
    # shortcut: scan latest snapshots for a few thousand employees; index claims at larger scale.
    candidates = []
    for employee, version in latest_profiles(session, principal):
        if department_ids is not None and employee.department_id not in department_ids:
            continue
        if configured is not None and employee.department_id not in configured:
            continue
        if any(
            field in fields and value is not None and getattr(employee, field) != value
            for field, value in (
                ("job_family", args.job_family),
                ("work_location", args.work_location),
            )
        ):
            continue
        if "hire_date" in fields and (
            (
                args.hire_date_from
                and (not employee.hire_date or employee.hire_date < args.hire_date_from)
            )
            or (
                args.hire_date_to
                and (not employee.hire_date or employee.hire_date > args.hire_date_to)
            )
        ):
            continue
        for claim in snapshot_claims(version):
            if claim.capability_definition_id not in definitions:
                continue
            if args.minimum_level is not None and (
                (claim.level is None and not args.include_ungraded)
                or (
                    claim.level is not None
                    and ranks.get(claim.level, -1) < ranks[args.minimum_level]
                )
            ):
                continue
            candidates.append((employee, claim))
    employee_ids = {e.id for e, _ in candidates}
    # Same open-appeal source and capability-key matching as profile/service.py.
    disputes = set(
        session.exec(
            select(ReviewAppeal.employee_id, CapabilityDefinition.key)
            .join(
                ChangeItem,
                (col(ChangeItem.id) == ReviewAppeal.item_id)
                & (col(ChangeItem.org_id) == ReviewAppeal.org_id),
            )
            .join(
                CapabilityDefinition,
                (col(CapabilityDefinition.id) == ChangeItem.capability_definition_id)
                & (col(CapabilityDefinition.org_id) == ChangeItem.org_id),
            )
            .where(
                ReviewAppeal.org_id == principal.org_id,
                col(ReviewAppeal.employee_id).in_(employee_ids),
                ReviewAppeal.status == AppealStatus.OPEN,
            )
        ).all()
    )
    evidence_ids = {ref.evidence_id for _, c in candidates for ref in c.evidence}
    evidence = {
        id: (subject, occurred)
        for id, subject, occurred in session.exec(
            select(
                Evidence.id, Evidence.subject_employee_id, Evidence.occurred_at
            ).where(
                Evidence.org_id == principal.org_id,
                col(Evidence.id).in_(evidence_ids),
                Evidence.status == EvidenceStatus.ACTIVE,
                Evidence.source != ASSIGNMENT_FEEDBACK_SOURCE,
            )
        ).all()
    }
    versions = list(
        session.exec(
            select(ProfileVersion)
            .where(
                ProfileVersion.org_id == principal.org_id,
                col(ProfileVersion.subject_employee_id).in_(employee_ids),
            )
            .order_by(col(ProfileVersion.revision))
        ).all()
    )
    first: dict[tuple[UUID, UUID], UUID] = {}
    for version in versions:
        for claim in snapshot_claims(version):
            first.setdefault((version.subject_employee_id, claim.id), version.id)
    publishers = {
        v.id: summary
        for v, summary in zip(
            versions, version_summaries(session, principal, versions), strict=True
        )
    }
    rows = []
    for employee, claim in candidates:
        status = (
            AssessmentStatus.DISPUTED
            if (employee.id, args.capability_key) in disputes
            else claim.assessment_status
        )
        if status == AssessmentStatus.UNKNOWN or (
            args.statuses and status not in args.statuses
        ):
            continue
        active = {
            ref.evidence_id
            for ref in claim.evidence
            if ref.evidence_id in evidence
            and evidence[ref.evidence_id][0] == employee.id
        }
        latest = max(
            (occurred for id in active if (occurred := evidence[id][1]) is not None),
            default=None,
        )
        if (
            args.evidence_from and (not latest or latest.date() < args.evidence_from)
        ) or (args.evidence_to and (not latest or latest.date() > args.evidence_to)):
            continue
        publisher = publishers[first[(employee.id, claim.id)]]
        rows.append(
            PeopleSearchRow(
                employee_id=employee.id,
                display_name=employee.display_name,
                employee_code=employee.employee_code,
                department_name=departments.get(employee.department_id)
                if employee.department_id
                else None,
                capability_key=capability.key,
                capability_name=capability.name,
                statement=claim.statement,
                level=claim.level,
                level_name=labels.get(claim.level, claim.level)
                if claim.level
                else None,
                status=status,
                evidence_count=len(active),
                latest_evidence_at=latest,
                published_by=publisher.published_by,
                published_at=publisher.published_at,
            )
        )
    rows.sort(
        key=lambda r: (
            -(ranks.get(r.level, -1) if r.level else -1),
            -(
                r.latest_evidence_at.timestamp()
                if r.latest_evidence_at
                else float("-inf")
            ),
            str(r.employee_id),
        )
    )
    return PeopleSearchResult(
        data=rows[args.skip : args.skip + args.limit], count=len(rows)
    )


def snapshot_claims(version: ProfileVersion) -> list[SnapshotClaim]:
    raw = version.snapshot.get("claims", [])
    return (
        [SnapshotClaim.model_validate(c) for c in raw] if isinstance(raw, list) else []
    )
