from uuid import UUID

from fastapi import APIRouter

from app.api.deps import SessionDep
from app.authz.deps import OrgPrincipalDep
from app.domains.capacity import settings, workload
from app.domains.capacity.schemas import (
    CapacitySettingsInput,
    MyWorkload,
    WorkloadAdjustInput,
    WorkloadDecisionInput,
    WorkloadList,
    WorkloadPublic,
    WorkloadRequestInput,
    WorkloadRequestList,
    WorkloadRequestPublic,
)

router = APIRouter(prefix="/orgs/{org_id}", tags=["capacity"])


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
def get_my_workload(principal: OrgPrincipalDep, session: SessionDep) -> MyWorkload:
    return workload.get_my_workload(principal, session)


@router.get("/capacity/workloads")
def list_workloads(
    principal: OrgPrincipalDep, session: SessionDep, department_id: UUID | None = None
) -> WorkloadList:
    return workload.list_workloads(principal, session, department_id)


@router.put("/capacity/workloads/{employee_id}")
def adjust_workload(
    principal: OrgPrincipalDep,
    session: SessionDep,
    employee_id: UUID,
    body: WorkloadAdjustInput,
) -> WorkloadPublic:
    result = workload.adjust_workload(principal, session, employee_id, body)
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
