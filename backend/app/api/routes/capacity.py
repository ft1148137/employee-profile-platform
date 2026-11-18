from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from sqlmodel import col, select

from app.api.deps import SessionDep
from app.authz.deps import OrgPrincipalDep
from app.domains.capacity import (
    assignment,
    demand,
    extension,
    project,
    proposal,
    settings,
    workload,
)
from app.domains.capacity.assignment_schemas import (
    AssignmentInput,
    AssignmentPublic,
    AssignmentUpdateInput,
    DemandAssignmentList,
    DepartedAssignmentCancelled,
    MyAssignmentList,
)
from app.domains.capacity.demand_schemas import (
    BorrowDecisionInput,
    BorrowRequest,
    CapacityReference,
    ChangeProposalInput,
    DemandBasis,
    DemandInput,
    DemandList,
    DemandListInput,
    DemandPublic,
    PlanReplyInput,
    ProjectDetail,
    ProjectInput,
    ProjectSummary,
    ProposalWithdrawInput,
    ReferenceInput,
    RespondInput,
)
from app.domains.capacity.extension_schemas import (
    ExtensionDecisionInput,
    ExtensionInput,
    ExtensionList,
    ExtensionPublic,
)
from app.domains.capacity.models import DemandOutcome
from app.domains.capacity.schemas import (
    CapacitySettingsInput,
    MyWorkload,
    UnavailabilityInput,
    UnavailabilityList,
    UnavailabilityPublic,
    UnavailabilityQuery,
    WorkloadDecisionInput,
    WorkloadList,
    WorkloadListQuery,
    WorkloadQuery,
    WorkloadRequestInput,
    WorkloadRequestList,
    WorkloadRequestPublic,
    WorkloadWeeksInput,
)
from app.domains.org.schemas import DepartmentsPublic
from app.domains.planning import service as planning
from app.domains.planning.models import (
    OptionKind,
    PlanningOption,
    PlanningSnapshot,
    PlanVerdict,
)

router = APIRouter(prefix="/orgs/{org_id}", tags=["capacity"])


@router.get("/capacity/unavailability")
def list_unavailability(
    principal: OrgPrincipalDep,
    session: SessionDep,
    query: Annotated[UnavailabilityQuery, Query()],
) -> UnavailabilityList:
    return workload.list_unavailability(principal, session, **query.model_dump())


@router.post("/capacity/unavailability")
def register_unavailability(
    principal: OrgPrincipalDep,
    session: SessionDep,
    body: UnavailabilityInput,
) -> UnavailabilityPublic:
    result = workload.register_unavailability(principal, session, body)
    session.commit()
    return result


@router.delete("/capacity/unavailability/{unavailability_id}")
def delete_unavailability(
    principal: OrgPrincipalDep,
    session: SessionDep,
    unavailability_id: UUID,
) -> UnavailabilityPublic:
    result = workload.delete_unavailability(principal, session, unavailability_id)
    session.commit()
    return result


@router.get("/capacity/demands/{demand_id}/assignments")
def list_demand_assignments(
    principal: OrgPrincipalDep, session: SessionDep, demand_id: UUID
) -> DemandAssignmentList:
    return assignment.list_demand_assignments(principal, session, demand_id)


@router.post("/capacity/demands/{demand_id}/assignments")
def create_assignment(
    principal: OrgPrincipalDep,
    session: SessionDep,
    demand_id: UUID,
    body: AssignmentInput,
) -> AssignmentPublic:
    result = assignment.create_assignment(principal, session, demand_id, body)
    session.commit()
    return result


@router.patch("/capacity/assignments/{assignment_id}")
def update_assignment(
    principal: OrgPrincipalDep,
    session: SessionDep,
    assignment_id: UUID,
    body: AssignmentUpdateInput,
) -> AssignmentPublic | DepartedAssignmentCancelled:
    result = assignment.update_assignment(principal, session, assignment_id, body)
    session.commit()
    return result


@router.get("/capacity/assignments/me")
def list_my_assignments(
    principal: OrgPrincipalDep, session: SessionDep
) -> MyAssignmentList:
    return assignment.list_my_assignments(principal, session)


@router.get("/capacity/departments")
def list_capacity_departments(
    principal: OrgPrincipalDep, session: SessionDep
) -> DepartmentsPublic:
    return demand.list_capacity_departments(principal, session)


@router.get("/capacity/reference")
def capacity_reference(
    principal: OrgPrincipalDep,
    session: SessionDep,
    query: Annotated[ReferenceInput, Query()],
) -> CapacityReference:
    return demand.capacity_reference(principal, session, **query.model_dump())


@router.post("/capacity/projects")
def submit_project(
    principal: OrgPrincipalDep, session: SessionDep, body: ProjectInput
) -> ProjectSummary:
    result = project.submit_project(principal, session, body)
    session.commit()
    return result


@router.get("/capacity/projects")
def list_projects(
    principal: OrgPrincipalDep, session: SessionDep
) -> list[ProjectSummary]:
    return project.list_projects(principal, session)


@router.get("/capacity/projects/{project_id}")
def get_project(
    principal: OrgPrincipalDep, session: SessionDep, project_id: UUID
) -> ProjectDetail:
    return project.get_project(principal, session, project_id)


@router.post("/capacity/demands")
def submit_demand(
    principal: OrgPrincipalDep, session: SessionDep, body: DemandInput
) -> DemandPublic:
    result = demand.submit_demand(principal, session, **body.model_dump())
    session.commit()
    return result


@router.get("/capacity/demands")
def list_demands(
    principal: OrgPrincipalDep,
    session: SessionDep,
    query: Annotated[DemandListInput, Query()],
) -> DemandList:
    return demand.list_demands(principal, session, **query.model_dump())


@router.get("/capacity/demands/{demand_id}")
def get_demand(
    principal: OrgPrincipalDep, session: SessionDep, demand_id: UUID
) -> DemandPublic:
    return demand.get_demand(principal, session, demand_id, include_previous=True)


@router.get("/capacity/demands/{demand_id}/basis")
def get_demand_basis(
    principal: OrgPrincipalDep, session: SessionDep, demand_id: UUID
) -> DemandBasis:
    return demand.get_demand_basis(principal, session, demand_id)


@router.post("/capacity/demands/{demand_id}/reply")
def reply_demand(
    principal: OrgPrincipalDep,
    session: SessionDep,
    demand_id: UUID,
    body: PlanReplyInput,
) -> DemandPublic:
    if body.option_id is None:
        if body.lender_department_id is not None or body.borrow_headcount is not None:
            raise proposal.ProposalConflict("只有借调方案可以指定借出部门和人数")
        result = demand.reply_demand(
            principal,
            session,
            demand_id,
            **body.model_dump(exclude={"lender_department_id", "borrow_headcount"}),
        )
    else:
        result = propose_plan(principal, session, demand_id, body)
    session.commit()
    return result


def propose_plan(
    principal: OrgPrincipalDep,
    session: SessionDep,
    demand_id: UUID,
    body: PlanReplyInput | ChangeProposalInput,
) -> DemandPublic:
    fields = {"lender_department_id", "borrow_headcount"}
    assert body.option_id is not None
    row = demand.demand_row(principal, session, demand_id, lock=True)
    if not demand.represents_department(principal, row.target_department_id):
        raise planning.PlanningStale()
    if isinstance(body, PlanReplyInput) and body.outcome == DemandOutcome.NOT_NOW:
        raise proposal.ProposalConflict("暂不能承接的答复不能带方案")
    found = session.exec(
        select(PlanningOption, PlanningSnapshot.case_id)
        .join(
            PlanningSnapshot,
            (col(PlanningSnapshot.org_id) == PlanningOption.org_id)
            & (col(PlanningSnapshot.id) == PlanningOption.snapshot_id),
        )
        .where(
            PlanningOption.org_id == principal.org_id,
            PlanningOption.id == body.option_id,
        )
    ).one_or_none()
    if found is None:
        raise planning.PlanningStale()
    saved, case_id = found
    _, snapshot, case_demand = planning.authorized_case(principal, session, case_id)
    if case_demand.id != demand_id or saved.snapshot_id != snapshot.id:
        raise planning.PlanningStale()
    if saved.verdict not in (PlanVerdict.FEASIBLE, PlanVerdict.CONDITIONAL):
        raise proposal.ProposalConflict("只有可行或有条件可行的方案可以提议")
    lines = proposal.plan_lines(
        principal,
        session,
        row,
        body,
        saved.params,
        borrow=saved.kind == OptionKind.BORROW,
    )
    current = planning.get_case(principal, session, case_id)
    if current.stale or current.snapshot_id != saved.snapshot_id:
        raise planning.PlanningStale()
    if isinstance(body, ChangeProposalInput):
        proposal.create(principal, session, row, body.option_id, lines, change=body)
    else:
        demand.reply_demand(
            principal,
            session,
            demand_id,
            **body.model_dump(
                exclude=fields
                | {"option_id", "outside_basis", "start_date", "end_date"}
            ),
            start_date=min(line.start_date for line in lines),
            end_date=max(line.end_date for line in lines),
        )
        proposal.create(principal, session, row, body.option_id, lines)
    return demand.get_demand(principal, session, demand_id, include_previous=True)


@router.post("/capacity/demands/{demand_id}/change")
def propose_change(
    principal: OrgPrincipalDep,
    session: SessionDep,
    demand_id: UUID,
    body: ChangeProposalInput,
) -> DemandPublic:
    result = propose_plan(principal, session, demand_id, body)
    session.commit()
    return result


@router.post("/capacity/demands/{demand_id}/respond")
def respond_demand(
    principal: OrgPrincipalDep, session: SessionDep, demand_id: UUID, body: RespondInput
) -> DemandPublic:
    try:
        result = demand.respond_demand(
            principal, session, demand_id, **body.model_dump()
        )
    except proposal.ProposalFailed:
        session.commit()
        raise
    session.commit()
    return result


@router.get("/capacity/borrow-requests")
def list_borrow_requests(
    principal: OrgPrincipalDep, session: SessionDep, mine: bool = False
) -> list[BorrowRequest]:
    return proposal.borrow_requests(principal, session, mine=mine)


@router.post("/capacity/borrow-requests/{line_id}/decision", status_code=204)
def decide_borrow_request(
    principal: OrgPrincipalDep,
    session: SessionDep,
    line_id: UUID,
    body: BorrowDecisionInput,
) -> None:
    try:
        proposal.decide_borrow(principal, session, line_id, body)
    except proposal.ProposalFailed:
        session.commit()
        raise
    session.commit()


@router.post("/capacity/proposals/{proposal_id}/withdraw")
def withdraw_proposal(
    principal: OrgPrincipalDep,
    session: SessionDep,
    proposal_id: UUID,
    body: ProposalWithdrawInput,
) -> DemandPublic:
    try:
        result = proposal.withdraw(principal, session, proposal_id, body.revision)
    except proposal.ProposalFailed:
        session.commit()
        raise
    session.commit()
    return result


@router.get("/capacity-settings")
def get_capacity_settings(
    principal: OrgPrincipalDep, session: SessionDep
) -> CapacitySettingsInput:
    return settings.read_settings(principal, session)


@router.put("/capacity-settings")
def put_capacity_settings(
    principal: OrgPrincipalDep, session: SessionDep, body: CapacitySettingsInput
) -> CapacitySettingsInput:
    result = settings.save_settings(principal, session, body)
    session.commit()
    return result


@router.get("/capacity/workloads/me")
def get_my_workload(
    principal: OrgPrincipalDep,
    session: SessionDep,
    query: Annotated[WorkloadQuery, Query()],
) -> MyWorkload:
    return workload.get_my_workload(principal, session, **query.model_dump())


@router.get("/capacity/workloads")
def list_workloads(
    principal: OrgPrincipalDep,
    session: SessionDep,
    query: Annotated[WorkloadListQuery, Query()],
) -> WorkloadList:
    return workload.list_workloads(principal, session, **query.model_dump())


@router.put("/capacity/weeks")
def set_capacity_weeks(
    principal: OrgPrincipalDep,
    session: SessionDep,
    body: WorkloadWeeksInput,
) -> WorkloadList:
    result = workload.set_weeks(principal, session, body)
    session.commit()
    return result


@router.post("/capacity/workloads/me/requests")
def request_workload_change(
    principal: OrgPrincipalDep, session: SessionDep, body: WorkloadRequestInput
) -> WorkloadRequestPublic:
    result = workload.request_workload_change(principal, session, **body.model_dump())
    session.commit()
    return result


@router.get("/capacity/workload-requests")
def list_workload_requests(
    principal: OrgPrincipalDep, session: SessionDep
) -> WorkloadRequestList:
    return workload.list_workload_requests(principal, session)


@router.post("/capacity/workload-requests/{request_id}/decision")
def decide_workload_request(
    principal: OrgPrincipalDep,
    session: SessionDep,
    request_id: UUID,
    body: WorkloadDecisionInput,
) -> WorkloadRequestPublic:
    result = workload.decide_workload_request(principal, session, request_id, body)
    session.commit()
    return result


@router.post("/capacity/assignments/{assignment_id}/extension-requests")
def request_extension(
    principal: OrgPrincipalDep,
    session: SessionDep,
    assignment_id: UUID,
    body: ExtensionInput,
) -> ExtensionPublic:
    result = extension.request_extension(
        principal, session, assignment_id, **body.model_dump()
    )
    session.commit()
    return result


@router.get("/capacity/extension-requests")
def list_extension_requests(
    principal: OrgPrincipalDep, session: SessionDep
) -> ExtensionList:
    return extension.list_extension_requests(principal, session)


@router.post("/capacity/extension-requests/{request_id}/decision")
def decide_extension_request(
    principal: OrgPrincipalDep,
    session: SessionDep,
    request_id: UUID,
    body: ExtensionDecisionInput,
) -> ExtensionPublic:
    result = extension.decide_extension_request(principal, session, request_id, body)
    session.commit()
    return result
