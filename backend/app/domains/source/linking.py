"""Shared identity assignment; callers own locks, evidence derivation and commit."""

from collections.abc import Sequence
from uuid import UUID

from sqlmodel import Session, select

from app.authz.principal import Principal
from app.domains.audit.service import record_audit
from app.domains.base import utc_now
from app.domains.org.models import Employee, EmploymentStatus, ExternalIdentity


def identity_audit(
    session: Session,
    principal: Principal,
    identity: ExternalIdentity,
    action: str,
    *,
    reason: str | None = None,
) -> None:
    record_audit(
        session,
        org_id=principal.org_id,
        actor_type=principal.actor_type,
        actor_id=str(principal.user_id) if principal.user_id else None,
        action=f"external_identity.{action}",
        target_type="external_identity",
        target_id=str(identity.id),
        details={
            "identity_id": str(identity.id),
            "employee_id": str(identity.employee_id),
            "source": identity.source,
            **({"reason": reason} if reason else {}),
        },
        flush=False,
    )


def assign_identity(
    session: Session,
    principal: Principal,
    identity: ExternalIdentity,
    employee_id: UUID,
    *,
    reason: str | None = None,
) -> None:
    identity.employee_id = employee_id
    identity.linked_by = (
        f"user:{principal.user_id}"
        if principal.user_id
        else "system_worker:email_match"
    )
    identity.linked_at = utc_now()
    session.add(identity)
    identity_audit(session, principal, identity, "linked", reason=reason)


def auto_link_identities(
    session: Session, org_id: UUID, identities: Sequence[ExternalIdentity]
) -> list[ExternalIdentity]:
    pending = [
        identity
        for identity in identities
        if identity.org_id == org_id
        and identity.employee_id is None
        and not identity.auto_link_blocked
        and identity.email
        and identity.email.strip()
    ]
    if not pending:
        return []
    matches: dict[str, list[UUID]] = {}
    for employee_id, email in session.exec(
        select(Employee.id, Employee.work_email).where(
            Employee.org_id == org_id,
            Employee.employment_status == EmploymentStatus.ACTIVE,
        )
    ):
        matches.setdefault((email or "").strip().lower(), []).append(employee_id)
    linked = []
    for identity in pending:
        employees = matches.get((identity.email or "").strip().lower(), [])
        if len(employees) == 1:
            assign_identity(
                session,
                Principal.system_worker(org_id),
                identity,
                employees[0],
                reason="email_match",
            )
            linked.append(identity)
    return linked
