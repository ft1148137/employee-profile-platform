from typing import Literal, cast
from uuid import UUID

from fastapi import HTTPException
from pydantic import JsonValue
from sqlalchemy import func, or_
from sqlmodel import Session, col, select

from app.authz.principal import Principal
from app.domains.audit.service import record_audit
from app.domains.base import utc_now
from app.domains.evidence.models import Evidence, EvidenceStatus
from app.domains.evidence.service import retract_evidence
from app.domains.org.models import (
    Department,
    Employee,
    EmploymentStatus,
    ExternalIdentity,
)
from app.domains.org.schemas import DepartmentSummary
from app.domains.source.schemas import (
    ExternalIdentitiesPublic,
    ExternalIdentityPublic,
    IdentityCandidate,
    IdentityEmployee,
    IdentityLinkResult,
    IdentityLinks,
    IdentityUnlinkResult,
)
from app.domains.work.ingest import IdentityNotFound, IngestCounts, rederive_identity
from app.domains.work.models import Task


class IdentityConflict(HTTPException):
    def __init__(self, detail: str) -> None:
        super().__init__(409, detail)


class InvalidIdentityEmployee(HTTPException):
    def __init__(self) -> None:
        super().__init__(422, "员工不属于本企业")


def list_identities(
    session: Session,
    org_id: UUID,
    *,
    status: Literal["unlinked", "linked", "all"],
    source: str | None,
    q: str | None,
    skip: int,
    limit: int,
) -> ExternalIdentitiesPublic:
    query = select(ExternalIdentity).where(ExternalIdentity.org_id == org_id)
    if status != "all":
        query = query.where(
            col(ExternalIdentity.employee_id).is_(None)
            if status == "unlinked"
            else col(ExternalIdentity.employee_id).is_not(None)
        )
    if source:
        query = query.where(ExternalIdentity.source == source)
    if q:
        query = query.where(
            or_(
                *(
                    col(field).icontains(q, autoescape=True)
                    for field in (
                        ExternalIdentity.external_id,
                        ExternalIdentity.display_name,
                        ExternalIdentity.email,
                    )
                )
            )
        )
    count = session.exec(select(func.count()).select_from(query.subquery())).one()
    identities = session.exec(
        query.order_by(col(ExternalIdentity.id)).offset(skip).limit(limit)
    ).all()
    # One employee/department query serves both linked employees and ambiguous candidates.
    employees = session.exec(
        select(Employee, Department)
        .outerjoin(
            Department,
            (col(Department.id) == Employee.department_id)
            & (col(Department.org_id) == Employee.org_id),
        )
        .where(Employee.org_id == org_id)
    ).all()
    public: dict[UUID, IdentityEmployee] = {}
    emails: dict[str, list[UUID]] = {}
    names: dict[str, list[UUID]] = {}
    for employee, department in employees:
        public[employee.id] = IdentityEmployee(
            **employee.model_dump(),
            department=DepartmentSummary.model_validate(department)
            if department
            else None,
        )
        if employee.work_email and employee.work_email.strip():
            emails.setdefault(employee.work_email.strip().lower(), []).append(
                employee.id
            )
        if employee.employment_status == EmploymentStatus.ACTIVE:
            names.setdefault(employee.display_name, []).append(employee.id)
    counts = dict(
        session.exec(
            select(ExternalIdentity.id, func.count(col(Task.id)))
            .join(
                Task,
                (col(Task.source) == ExternalIdentity.source)
                & (col(Task.org_id) == ExternalIdentity.org_id)
                & col(Task.attributes).contains(
                    func.jsonb_build_object(
                        "people",
                        func.jsonb_build_array(
                            func.jsonb_build_object("id", ExternalIdentity.external_id)
                        ),
                    )
                ),
            )
            .where(
                ExternalIdentity.org_id == org_id,
                col(ExternalIdentity.id).in_([i.id for i in identities]),
            )
            .group_by(col(ExternalIdentity.id))
        ).all()
    )
    data = []
    for identity in identities:
        candidate = None
        if identity.employee_id is None:
            candidates: list[tuple[Literal["email", "name"], list[UUID]]] = [
                ("email", emails.get((identity.email or "").strip().lower(), [])),
                ("name", names.get(identity.display_name or "", [])),
            ]
            for reason, matches in candidates:
                if len(matches) == 1:
                    candidate = IdentityCandidate(
                        **public[matches[0]].model_dump(), reason=reason
                    )
                    break
        data.append(
            ExternalIdentityPublic(
                **identity.model_dump(),
                source_label="Jira"
                if identity.source == "jira"
                else identity.source.removeprefix("csv:"),
                record_count=counts.get(identity.id, 0),
                employee=public.get(identity.employee_id)
                if identity.employee_id
                else None,
                candidate=candidate,
            )
        )
    return ExternalIdentitiesPublic(data=data, count=count)


def identity_audit(
    session: Session, principal: Principal, identity: ExternalIdentity, action: str
) -> None:
    record_audit(
        session,
        org_id=principal.org_id,
        actor_type=principal.actor_type,
        actor_id=str(principal.user_id),
        action=f"external_identity.{action}",
        target_type="external_identity",
        target_id=str(identity.id),
        details={
            "identity_id": str(identity.id),
            "employee_id": str(identity.employee_id),
            "source": identity.source,
        },
    )


def link_identities(
    session: Session, principal: Principal, body: IdentityLinks
) -> IdentityLinkResult:
    identities = {
        i.id: i
        for i in session.exec(
            select(ExternalIdentity)
            .where(
                ExternalIdentity.org_id == principal.org_id,
                col(ExternalIdentity.id).in_([link.identity_id for link in body.links]),
            )
            .order_by(col(ExternalIdentity.id))
            .with_for_update()
        )
    }
    employees = set(
        session.exec(
            select(Employee.id).where(
                Employee.org_id == principal.org_id,
                col(Employee.id).in_([link.employee_id for link in body.links]),
            )
        ).all()
    )
    pending: dict[UUID, UUID] = {}
    for link in body.links:
        if link.identity_id not in identities:
            raise IdentityNotFound("账号不存在")
        if link.employee_id not in employees:
            raise InvalidIdentityEmployee()
        current = pending.get(
            link.identity_id, identities[link.identity_id].employee_id
        )
        if current is not None and current != link.employee_id:
            raise IdentityConflict("账号已关联到其他员工，请先解除")
        pending[link.identity_id] = link.employee_id
    linked, counts = 0, IngestCounts()
    for identity_id, employee_id in pending.items():
        identity = identities[identity_id]
        if identity.employee_id == employee_id:
            continue
        identity.employee_id, identity.linked_by, identity.linked_at = (
            employee_id,
            f"user:{principal.user_id}",
            utc_now(),
        )
        session.flush()
        result = rederive_identity(session, org_id=principal.org_id, identity=identity)
        for field in IngestCounts.model_fields:
            setattr(counts, field, getattr(counts, field) + getattr(result, field))
        identity_audit(session, principal, identity, "linked")
        linked += 1
    session.commit()
    return IdentityLinkResult(linked=linked, counts=counts)


def unlink_identity(
    session: Session, principal: Principal, identity_id: UUID
) -> IdentityUnlinkResult:
    identity = session.exec(
        select(ExternalIdentity)
        .where(
            ExternalIdentity.org_id == principal.org_id,
            ExternalIdentity.id == identity_id,
        )
        .with_for_update()
    ).one_or_none()
    if identity is None:
        raise IdentityNotFound("账号不存在")
    if identity.employee_id is None:
        raise IdentityConflict("账号尚未关联")
    tasks = session.exec(
        select(Task)
        .where(
            Task.org_id == principal.org_id,
            Task.source == identity.source,
            col(Task.attributes).contains({"people": [{"id": identity.external_id}]}),
        )
        .order_by(col(Task.id))
        .with_for_update()
    ).all()
    other_accounts = set(
        session.exec(
            select(ExternalIdentity.external_id).where(
                ExternalIdentity.org_id == principal.org_id,
                ExternalIdentity.source == identity.source,
                ExternalIdentity.employee_id == identity.employee_id,
                ExternalIdentity.id != identity.id,
            )
        ).all()
    )
    keys = [
        task.external_key
        for task in tasks
        if not any(
            isinstance(person, dict) and person.get("id") in other_accounts
            for person in cast(list[dict[str, JsonValue]], task.attributes["people"])
        )
    ]
    evidence = session.exec(
        select(Evidence).where(
            Evidence.org_id == principal.org_id,
            Evidence.source == identity.source,
            Evidence.subject_employee_id == identity.employee_id,
            col(Evidence.source_ref).in_(keys),
            Evidence.status == EvidenceStatus.ACTIVE,
        )
    ).all()
    for item in evidence:
        retract_evidence(
            session,
            evidence=item,
            reason="管理员解除了账号对应",
            actor_type=principal.actor_type,
            actor_id=str(principal.user_id),
        )
    identity_audit(session, principal, identity, "unlinked")
    identity.employee_id = identity.linked_by = identity.linked_at = None
    session.commit()
    return IdentityUnlinkResult(evidence_retracted=len(evidence))
