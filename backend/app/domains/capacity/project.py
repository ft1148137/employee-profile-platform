from uuid import UUID

from sqlmodel import Session, col, or_, select

from app.authz.principal import Principal
from app.domains.capacity import demand
from app.domains.capacity.demand_schemas import (
    ProjectDetail,
    ProjectInput,
    ProjectRole,
    ProjectSummary,
)
from app.domains.capacity.models import (
    CapacityDemand,
    CapacityDemandDependency,
    CapacityProject,
)
from app.domains.capacity.schemas import Via
from app.domains.capacity.settings import CapacityNotFound, require_enabled
from app.domains.org.models import Department

PROJECT_NOT_FOUND = "未找到这个项目需求，或你没有权限查看。"


def submit_project(
    principal: Principal,
    session: Session,
    body: ProjectInput,
    *,
    via: Via = "web",
    client_name: str | None = None,
) -> ProjectSummary:
    requester = demand.require_requesting_department(
        principal, session, body.requesting_department_id
    )
    row = CapacityProject(
        org_id=principal.org_id,
        requesting_department_id=body.requesting_department_id,
        requester_user_id=requester,
        title=body.title,
        description=body.description,
    )
    with session.begin_nested():
        session.add(row)
        session.flush()
        roles: list[CapacityDemand] = []
        for role in body.roles:
            roles.append(
                demand.create_demand(
                    principal,
                    session,
                    role.as_demand(body.requesting_department_id),
                    project_id=row.id,
                    role=role,
                    via=via,
                    client_name=client_name,
                )
            )
        session.add_all(
            CapacityDemandDependency(
                org_id=principal.org_id,
                project_id=row.id,
                demand_id=roles[index].id,
                depends_on_demand_id=roles[parent].id,
            )
            for index, role in enumerate(body.roles)
            for parent in role.depends_on
        )
    requesting = demand.department(
        session, principal.org_id, row.requesting_department_id
    )
    return project_summary(row, requesting.name)


def project_summary(row: CapacityProject, department_name: str) -> ProjectSummary:
    return ProjectSummary(
        id=row.id,
        title=row.title,
        created_at=row.created_at,
        requesting_department_name=department_name,
    )


def list_projects(principal: Principal, session: Session) -> list[ProjectSummary]:
    require_enabled(principal, session)
    query = (
        select(CapacityProject, Department.name)
        .join(
            Department,
            (col(Department.org_id) == CapacityProject.org_id)
            & (col(Department.id) == CapacityProject.requesting_department_id),
        )
        .where(CapacityProject.org_id == principal.org_id)
    )
    if not principal.is_org_admin:
        query = query.where(
            or_(
                CapacityProject.requester_user_id == principal.user_id,
                col(CapacityProject.requesting_department_id).in_(
                    principal.managed_department_ids
                ),
            )
        )
    return [
        project_summary(row, name)
        for row, name in session.exec(
            query.order_by(
                col(CapacityProject.created_at).desc(), col(CapacityProject.id)
            )
        )
    ]


def get_project(
    principal: Principal, session: Session, project_id: UUID
) -> ProjectDetail:
    require_enabled(principal, session)
    project = session.exec(
        select(CapacityProject).where(
            CapacityProject.org_id == principal.org_id, CapacityProject.id == project_id
        )
    ).one_or_none()
    if project is None:
        raise CapacityNotFound(PROJECT_NOT_FOUND)
    requester_view = (
        project.requester_user_id == principal.user_id
        or demand.represents_department(principal, project.requesting_department_id)
    )
    rows = session.exec(
        select(CapacityDemand, Department.name)
        .join(
            Department,
            (col(Department.org_id) == CapacityDemand.org_id)
            & (col(Department.id) == CapacityDemand.target_department_id),
        )
        .where(
            CapacityDemand.org_id == principal.org_id,
            CapacityDemand.project_id == project.id,
        )
        .order_by(col(CapacityDemand.created_at), col(CapacityDemand.id))
    ).all()
    if not requester_view and not any(
        demand.represents_department(principal, row.target_department_id)
        for row, _ in rows
    ):
        raise CapacityNotFound(PROJECT_NOT_FOUND)

    demands = (
        {
            item.id: item
            for item in demand.demands_public(
                principal, session, [row for row, _ in rows], requester_view=True
            )
        }
        if requester_view
        else {}
    )
    requesting = demand.department(
        session, principal.org_id, project.requesting_department_id
    )
    return ProjectDetail(
        **project_summary(project, requesting.name).model_dump(),
        description=project.description if requester_view else None,
        roles=[
            ProjectRole(
                **demand.project_role_summary(row, name).model_dump(),
                demand=demands.get(row.id),
            )
            for row, name in rows
        ],
    )
