from typing import Literal
from uuid import UUID

from sqlmodel import Session

from app.authz.principal import Principal
from app.domains.capacity import demand, project, proposal
from app.domains.capacity.assignment import list_my_assignments
from app.domains.capacity.demand import (
    capacity_reference,
    get_demand,
    get_demand_basis,
    list_capacity_departments,
    list_demands,
    reply_demand,
    submit_demand,
)
from app.domains.capacity.demand_schemas import (
    DemandIdInput,
    DemandInput,
    DemandListInput,
    DemandPublic,
    ProjectIdInput,
    ProjectList,
    ReferenceInput,
    ReplyDemandInput,
    RespondDemandInput,
)
from app.domains.capacity.extension import request_extension
from app.domains.capacity.extension_schemas import RequestExtensionInput
from app.domains.capacity.schemas import (
    CapacityInput,
    ReasonCode,
    Via,
    WorkloadRequestInput,
)
from app.domains.capacity.settings import CapacityForbidden
from app.domains.capacity.workload import get_my_workload, request_workload_change
from app.domains.employee_tools import EmployeeTool


class McpProposalAcceptanceForbidden(CapacityForbidden):
    def __init__(self) -> None:
        super().__init__()
        self.detail = "接受方案会锁定对方资源，请在网页或助手的确认卡里操作"


def list_projects(principal: Principal, session: Session) -> ProjectList:
    rows = project.list_projects(principal, session)
    return ProjectList(data=rows, count=len(rows))


def respond_demand(
    principal: Principal,
    session: Session,
    demand_id: UUID,
    action: Literal["accept", "follow_up", "withdraw"],
    revision: int,
    text: str | None = None,
    *,
    reason_code: ReasonCode | None = None,
    via: Via = "web",
    client_name: str | None = None,
) -> DemandPublic:
    row = demand.demand_row(principal, session, demand_id, lock=True)
    if (
        via == "mcp"
        and action == "accept"
        and principal.user_id is not None
        and (
            row.requester_user_id == principal.user_id
            or row.requesting_department_id in principal.managed_department_ids
        )
    ):
        current = proposal.latest(session, principal.org_id, demand_id, lock=True)
        if current is not None and current.status == "pending":
            raise McpProposalAcceptanceForbidden()
    return demand.respond_demand(
        principal,
        session,
        demand_id,
        action,
        revision,
        text,
        reason_code=reason_code,
        via=via,
        client_name=client_name,
    )


CAPACITY_TOOLS = (
    EmployeeTool(
        "list_capacity_departments",
        "选择目标部门，只列部门名称和层级",
        CapacityInput,
        list_capacity_departments,
    ),
    EmployeeTool(
        "capacity_reference",
        "查看目标部门的能力和负荷汇总，不含员工名单",
        ReferenceInput,
        capacity_reference,
    ),
    EmployeeTool(
        "submit_demand", "向其他部门提交需求", DemandInput, submit_demand, True
    ),
    EmployeeTool(
        "list_demands", "查看我方发出或收到的需求", DemandListInput, list_demands
    ),
    EmployeeTool("get_demand", "查看需求详情和事件时间线", DemandIdInput, get_demand),
    EmployeeTool(
        "list_projects",
        "查看我发起或本部门作为需求方的多角色项目需求列表",
        CapacityInput,
        list_projects,
    ),
    EmployeeTool(
        "get_project",
        "查看多角色项目详情、各角色进展与排程延期影响分析",
        ProjectIdInput,
        project.get_project,
    ),
    EmployeeTool(
        "get_demand_basis",
        "查看本部门需求依据和个人负荷，仅限答复方",
        DemandIdInput,
        get_demand_basis,
    ),
    EmployeeTool(
        "reply_demand",
        "代表本部门答复需求并确认承诺日期；暂不能承接时须选择原因代码，其他须写说明；MCP 不提议方案或预占人员",
        ReplyDemandInput,
        reply_demand,
        True,
    ),
    EmployeeTool(
        "respond_demand",
        "接受、追问或撤回我方需求；有待处理提议时追问为拒绝（须填写原因代码和说明），接受须点击网页或助手确认卡",
        RespondDemandInput,
        respond_demand,
        True,
    ),
    EmployeeTool(
        "get_my_workload",
        "查看我的 Jira 参考值和经理确认负荷",
        CapacityInput,
        get_my_workload,
    ),
    EmployeeTool(
        "request_workload_change",
        "申请调整本人指定周范围的平台外工作占比，周一开始，最多26周，等待经理审批",
        WorkloadRequestInput,
        request_workload_change,
        True,
    ),
    EmployeeTool(
        "list_my_assignments",
        "查看分配给我的工作和完成日期",
        CapacityInput,
        list_my_assignments,
    ),
    EmployeeTool(
        "request_extension",
        "申请延后本人分配的完成日期",
        RequestExtensionInput,
        request_extension,
        True,
    ),
)
