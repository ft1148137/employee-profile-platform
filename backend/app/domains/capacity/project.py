from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from datetime import date
from graphlib import CycleError, TopologicalSorter
from uuid import UUID

from fastapi import HTTPException
from sqlmodel import Session, col, or_, select

from app.authz.principal import Principal
from app.domains.capability.service import get_schema, level_exists, list_capabilities
from app.domains.capacity import demand
from app.domains.capacity.demand_schemas import (
    ProjectDetail,
    ProjectImpact,
    ProjectImpactReason,
    ProjectInput,
    ProjectRole,
    ProjectRoleSummary,
    ProjectSchedule,
    ProjectSummary,
    ScheduledProjectRole,
)
from app.domains.capacity.models import (
    CapacityDemand,
    CapacityDemandDependency,
    CapacityDemandEvent,
    CapacityProject,
    DemandEventKind,
    DemandOutcome,
    DemandStatus,
)
from app.domains.capacity.schemas import Via
from app.domains.capacity.settings import CapacityNotFound, require_enabled
from app.domains.org.models import Department

PROJECT_NOT_FOUND = "未找到这个项目需求，或你没有权限查看。"


class ProjectCapabilityInvalid(HTTPException):
    def __init__(self, role_name: str, detail: str) -> None:
        super().__init__(
            422, f"角色「{role_name}」的{detail}，请从当前能力词典重新选择"
        )


class InvalidProjectDependencies(ValueError):
    pass


def analyze_project(
    roles: Sequence[ProjectRoleSummary],
    dependencies: Mapping[UUID, Collection[UUID]],
    outcomes: Mapping[UUID, DemandOutcome | None],
) -> ProjectSchedule:
    """Analyze one nonempty project's roles and direct predecessors, keyed by demand ID.

    Preserve input order within stages. Planned dates use commitments or the demand
    window. The forecast shifts dependent roles without shortening their duration.
    """
    by_id = {role.id: role for role in roles}
    graph = {role.id: tuple(dependencies.get(role.id, ())) for role in roles}
    if not by_id.keys() >= dependencies.keys() or any(
        parent not in by_id for parents in graph.values() for parent in parents
    ):
        raise InvalidProjectDependencies("前置角色必须属于同一项目")
    sorter = TopologicalSorter(graph)
    try:
        sorter.prepare()
    except CycleError as error:
        raise InvalidProjectDependencies("前置角色不能形成循环") from error

    planned_ends = {
        role.id: (role.committed_end_date or role.window_end).toordinal()
        for role in roles
    }
    forecast_ends: dict[UUID, int] = {}
    order = {role.id: i for i, role in enumerate(roles)}
    stages: list[list[ScheduledProjectRole]] = []
    affected: dict[UUID, list[ProjectImpactReason]] = {}
    while sorter.is_active():
        ready = sorted(sorter.get_ready(), key=order.__getitem__)
        stage = []
        for role_id in ready:
            role = by_id[role_id]
            parents = graph[role_id]
            earliest = max((planned_ends[p] + 1 for p in parents), default=0)
            start = role.committed_start_date or role.window_start
            end = role.committed_end_date or role.window_end
            forecast_start = max(
                start.toordinal(),
                max((forecast_ends[p] + 1 for p in parents), default=0),
            )
            forecast_end = end.toordinal() + forecast_start - start.toordinal()
            forecast_ends[role_id] = forecast_end
            stage.append(
                ScheduledProjectRole(
                    role=role,
                    planned_start=start,
                    planned_end=end,
                    earliest_start=calendar_date(earliest),
                    forecast_start=calendar_date(forecast_start),
                    forecast_end=calendar_date(forecast_end),
                )
            )
            reasons: list[ProjectImpactReason] = []
            outcome = outcomes.get(role_id)
            if role.status in (DemandStatus.REPLIED, DemandStatus.ACCEPTED):
                if outcome == DemandOutcome.PARTIAL:
                    reasons.append("partial")
                if outcome == DemandOutcome.NOT_NOW:
                    reasons.append("not_now")
            if end > role.window_end:
                reasons.append("late_commitment")
            if start.toordinal() < earliest:
                reasons.append("dependency")
            if reasons:
                affected[role_id] = reasons
        stages.append(stage)
        sorter.done(*ready)

    downstream: dict[UUID, set[UUID]] = defaultdict(set)
    for role_id in reversed(forecast_ends):
        for parent in graph[role_id]:
            downstream[parent].add(role_id)
            downstream[parent].update(downstream[role_id])
    window_end = max(role.window_end for role in roles)
    forecast_end = max(forecast_ends.values())
    return ProjectSchedule(
        stages=stages,
        impacts=[
            ProjectImpact(
                role=by_id[role_id],
                reasons=reasons,
                downstream=[
                    by_id[item] for item in forecast_ends if item in downstream[role_id]
                ],
            )
            for role_id, reasons in affected.items()
        ],
        window_end=window_end,
        forecast_end=calendar_date(forecast_end),
        delay_days=max(0, forecast_end - window_end.toordinal()),
    )


def calendar_date(ordinal: int) -> date | None:
    return date.fromordinal(ordinal) if 0 < ordinal <= date.max.toordinal() else None


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
    if any(role.extra_capabilities for role in body.roles):
        schema = get_schema(session, principal.org_id)
        capabilities = {
            row.key: row
            for row in list_capabilities(session, principal.org_id, schema.id)
        }
        for role in body.roles:
            for extra in role.extra_capabilities:
                capability = capabilities.get(extra.key)
                if capability is None:
                    raise ProjectCapabilityInvalid(
                        role.role_name,
                        f"额外能力「{extra.key}」不在当前启用的能力词典中",
                    )
                if not level_exists(capability.level_rubric, extra.minimum_level):
                    raise ProjectCapabilityInvalid(
                        role.role_name, f"额外能力「{capability.name}」的最低等级已失效"
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


def project_role_rows(
    session: Session, org_id: UUID, project_id: UUID
) -> list[tuple[CapacityDemand, str]]:
    return list(
        session.exec(
            select(CapacityDemand, Department.name)
            .join(
                Department,
                (col(Department.org_id) == CapacityDemand.org_id)
                & (col(Department.id) == CapacityDemand.target_department_id),
            )
            .where(
                CapacityDemand.org_id == org_id,
                CapacityDemand.project_id == project_id,
            )
            .order_by(col(CapacityDemand.created_at), col(CapacityDemand.id))
        )
    )


def project_dependencies(
    session: Session, org_id: UUID, project_id: UUID
) -> dict[UUID, list[UUID]]:
    prerequisites: dict[UUID, list[UUID]] = defaultdict(list)
    for dependency in session.exec(
        select(CapacityDemandDependency).where(
            CapacityDemandDependency.org_id == org_id,
            CapacityDemandDependency.project_id == project_id,
        )
    ):
        prerequisites[dependency.demand_id].append(dependency.depends_on_demand_id)
    return prerequisites


def unavailability_impact(
    session: Session, affected: CapacityDemand, projected_end: date
) -> tuple[list[str], int]:
    assert affected.project_id is not None
    roles = [
        demand.project_role_summary(row, department)
        for row, department in project_role_rows(
            session, affected.org_id, affected.project_id
        )
    ]
    dependencies = project_dependencies(session, affected.org_id, affected.project_id)
    baseline = analyze_project(roles, dependencies, {})
    forecast = analyze_project(
        [
            role.model_copy(
                update={
                    "committed_end_date": max(
                        projected_end, role.committed_end_date or role.window_end
                    ),
                }
            )
            if role.id == affected.id
            else role
            for role in roles
        ],
        dependencies,
        {},
    )
    original = {
        item.role.id: item.forecast_end for stage in baseline.stages for item in stage
    }
    return [
        f"{item.role.role_name}（{item.role.target_department_name}）"
        for stage in forecast.stages
        for item in stage
        if item.role.id != affected.id and item.forecast_end != original[item.role.id]
    ], forecast.delay_days


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
    rows = project_role_rows(session, principal.org_id, project.id)
    if not requester_view and not any(
        demand.represents_department(principal, row.target_department_id)
        for row, _ in rows
    ):
        raise CapacityNotFound(PROJECT_NOT_FOUND)

    prerequisites = project_dependencies(session, principal.org_id, project.id)
    outcomes = {
        demand_id: DemandOutcome(value) if value is not None else None
        for demand_id, value in session.exec(
            select(CapacityDemandEvent.demand_id, CapacityDemandEvent.outcome)
            .where(
                CapacityDemandEvent.org_id == principal.org_id,
                col(CapacityDemandEvent.demand_id).in_([row.id for row, _ in rows]),
                CapacityDemandEvent.kind == DemandEventKind.REPLIED,
            )
            .order_by(col(CapacityDemandEvent.created_at), col(CapacityDemandEvent.id))
        )
    }
    roles = [demand.project_role_summary(row, name) for row, name in rows]
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
                **role.model_dump(),
                outcome=outcomes.get(role.id)
                if role.status in (DemandStatus.REPLIED, DemandStatus.ACCEPTED)
                else None,
                demand=demands.get(role.id),
            )
            for role in roles
        ],
        schedule=analyze_project(roles, prerequisites, outcomes),
    )
