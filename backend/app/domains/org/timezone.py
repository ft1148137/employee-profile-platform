from uuid import UUID

from sqlmodel import Session, select

from app.authz.principal import Principal
from app.domains.audit.service import record_audit
from app.domains.org.models import Organization
from app.domains.org.schemas import OrganizationTimezone


def read_timezone(session: Session, org_id: UUID) -> OrganizationTimezone:
    value = session.exec(
        select(Organization.timezone).where(Organization.id == org_id)
    ).one()
    return OrganizationTimezone(timezone=value)


def save_timezone(
    session: Session, principal: Principal, body: OrganizationTimezone
) -> OrganizationTimezone:
    org = session.exec(
        select(Organization)
        .where(Organization.id == principal.org_id)
        .with_for_update()
    ).one()
    if org.timezone != body.timezone:
        before = org.timezone
        org.timezone = body.timezone
        session.add(org)
        record_audit(
            session,
            org_id=principal.org_id,
            actor_type=principal.actor_type,
            actor_id=str(principal.user_id),
            action="org.timezone_updated",
            target_type="organization",
            target_id=str(principal.org_id),
            details={"before": {"timezone": before}, "after": body.model_dump()},
        )
    session.commit()
    return body
