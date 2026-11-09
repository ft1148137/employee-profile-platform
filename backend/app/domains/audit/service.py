from datetime import datetime
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import String, cast, func, or_
from sqlmodel import Session, col, select

from app.core.config import settings
from app.domains.audit.models import ActorKind, AuditEvent
from app.domains.audit.schemas import AuditActor, AuditEventPublic, AuditEventsPublic
from app.domains.capability.models import CapabilitySchemaVersion
from app.domains.capability.templates import load_catalog
from app.domains.org.models import (
    Department,
    Organization,
    OrgMembership,
    RoleGrant,
    RosterImport,
)
from app.models import User


def record_audit(
    session: Session,
    *,
    org_id: UUID | None,
    actor_type: ActorKind,
    actor_id: str | None,
    action: str,
    target_type: str,
    target_id: str | None,
    details: dict[str, JsonValue],
    flush: bool = True,
) -> AuditEvent:
    """Append an audit event in the caller's transaction without committing."""
    event = AuditEvent(
        org_id=org_id,
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        details=details,
    )
    session.add(event)
    if flush:
        session.flush()
    return event


def parse_id(value: object) -> UUID | None:
    try:
        return UUID(str(value))
    except ValueError:
        return None


def user_label(user: User) -> str:
    return f"{user.full_name}（{user.username}）" if user.full_name else user.username


def list_events(
    session: Session,
    org_id: UUID,
    action: str | None = None,
    actor_user_id: UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    skip: int = 0,
    limit: int = 50,
    *,
    target_type: str | None = None,
    target_ids: list[str] | None = None,
    employee_id: UUID | None = None,
) -> AuditEventsPublic:
    query = select(AuditEvent).where(AuditEvent.org_id == org_id)
    if target_type is not None:
        query = query.where(col(AuditEvent.target_type) == target_type)
    if target_ids is not None:
        query = query.where(col(AuditEvent.target_id).in_(target_ids))
    if employee_id is not None:
        memberships = select(OrgMembership.id).where(
            OrgMembership.org_id == org_id,
            OrgMembership.employee_id == employee_id,
        )
        user_ids_query = select(cast(OrgMembership.user_id, String)).where(
            OrgMembership.org_id == org_id,
            OrgMembership.employee_id == employee_id,
        )
        grant_ids = select(cast(RoleGrant.id, String)).where(
            RoleGrant.org_id == org_id,
            col(RoleGrant.membership_id).in_(memberships),
        )
        # shortcut: deleted legacy grants without user_id cannot be attributed,
        # revisit only if an authoritative historical mapping becomes available.
        query = query.where(
            or_(
                (col(AuditEvent.target_type) == "employee")
                & (col(AuditEvent.target_id) == str(employee_id)),
                (col(AuditEvent.target_type) == "user")
                & col(AuditEvent.target_id).in_(user_ids_query),
                (col(AuditEvent.target_type) == "role_grant")
                & or_(
                    col(AuditEvent.details)["user_id"].astext.in_(user_ids_query),
                    col(AuditEvent.target_id).in_(grant_ids),
                ),
                (col(AuditEvent.target_type) == "external_identity")
                & (col(AuditEvent.details)["employee_id"].astext == str(employee_id)),
            )
        )
    if action:
        query = query.where(
            col(AuditEvent.action).startswith(action, autoescape=True)
            if action.endswith(".")
            else AuditEvent.action == action
        )
    if actor_user_id:
        query = query.where(
            AuditEvent.actor_type == ActorKind.USER,
            AuditEvent.actor_id == str(actor_user_id),
        )
    if since:
        query = query.where(AuditEvent.created_at >= since)
    if until:
        query = query.where(AuditEvent.created_at < until)
    count = session.exec(select(func.count()).select_from(query.subquery())).one()
    events = session.exec(
        query.order_by(col(AuditEvent.created_at).desc(), col(AuditEvent.id).desc())
        .offset(skip)
        .limit(limit)
    ).all()
    event_target_ids = {parse_id(e.target_id) for e in events}
    # Old role events only retain the grant id; resolve surviving grants in one query.
    grants = {
        str(g.id): (g, user_id)
        for g, user_id in session.exec(
            select(RoleGrant, OrgMembership.user_id)
            .join(OrgMembership, col(OrgMembership.id) == RoleGrant.membership_id)
            .where(
                RoleGrant.org_id == org_id,
                OrgMembership.org_id == org_id,
                col(RoleGrant.id).in_(event_target_ids),
            )
        ).all()
    }
    role_details = {}
    for e in events:
        if e.target_type == "role_grant":
            details = e.details
            if "user_id" not in details and e.target_id in grants:
                grant, user_id = grants[e.target_id]
                details = {
                    "user_id": str(user_id),
                    "role": grant.role.value,
                    "department_id": str(grant.department_id)
                    if grant.department_id
                    else None,
                }
            role_details[e.id] = details
    user_ids = event_target_ids | {
        parse_id(e.actor_id) for e in events if e.actor_type == ActorKind.USER
    }
    user_ids |= {parse_id(d.get("user_id")) for d in role_details.values()}
    users: dict[UUID | None, User] = {
        u.id: u
        for u in session.exec(
            select(User)
            .join(OrgMembership)
            .where(OrgMembership.org_id == org_id, col(User.id).in_(user_ids))
        ).all()
    }
    departments: dict[UUID | None, Department] = {
        d.id: d
        for d in session.exec(
            select(Department).where(Department.org_id == org_id)
        ).all()
    }
    orgs = {
        o.id: o
        for o in session.exec(
            select(Organization).where(
                Organization.id == org_id, col(Organization.id).in_(event_target_ids)
            )
        ).all()
    }
    schemas = {
        v.id: v
        for v in session.exec(
            select(CapabilitySchemaVersion).where(
                CapabilitySchemaVersion.org_id == org_id,
                col(CapabilitySchemaVersion.id).in_(event_target_ids),
            )
        ).all()
    }
    imports = {
        r.id: r
        for r in session.exec(
            select(RosterImport).where(
                RosterImport.org_id == org_id,
                col(RosterImport.id).in_(event_target_ids),
            )
        ).all()
    }
    templates = {
        t.entry.id: t.entry.name
        for t in load_catalog(settings.CAPABILITY_TEMPLATES_DIR)
    }
    data = []
    for e in events:
        target_id = parse_id(e.target_id)
        label = None
        if e.target_type == "user" and target_id in users:
            label = user_label(users[target_id])
        elif e.target_type == "role_grant":
            details = role_details[e.id]
            user = users.get(parse_id(details.get("user_id")))
            role = details.get("role")
            if user and role in ("org_admin", "dept_manager"):
                label = f"{user_label(user)} · " + (
                    "企业管理员" if role == "org_admin" else "部门经理"
                )
                if role == "dept_manager":
                    department = departments.get(parse_id(details.get("department_id")))
                    names: list[str] = []
                    visited: set[UUID] = set()
                    while department and department.id not in visited:
                        visited.add(department.id)
                        names.insert(0, department.name)
                        department = departments.get(department.parent_department_id)
                    label = f"{label} · {' / '.join(names)}" if names else None
        elif (
            e.target_type == "organization"
            and target_id is not None
            and target_id in orgs
        ):
            label = orgs[target_id].name
        elif (
            e.target_type == "capability_schema_version"
            and target_id is not None
            and target_id in schemas
        ):
            name = templates.get(str(e.details.get("template_id")))
            label = f"第 {schemas[target_id].version} 版"
            if name:
                label = f"{name} · {label}"
        elif (
            e.target_type == "roster_import"
            and target_id is not None
            and target_id in imports
        ):
            label = imports[target_id].filename
        actor = (
            users.get(parse_id(e.actor_id)) if e.actor_type == ActorKind.USER else None
        )
        data.append(
            AuditEventPublic(
                **e.model_dump(exclude={"org_id", "actor_id"}),
                actor=AuditActor(
                    user_id=actor.id, username=actor.username, full_name=actor.full_name
                )
                if actor
                else None,
                target_label=label,
            )
        )
    return AuditEventsPublic(data=data, count=count)
