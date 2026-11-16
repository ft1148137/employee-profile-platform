from app.domains.capacity.assignment import list_my_assignments
from app.domains.capacity.demand import (
    capacity_reference,
    get_demand,
    get_demand_basis,
    list_capacity_departments,
    list_demands,
    reply_demand,
    respond_demand,
    submit_demand,
)
from app.domains.capacity.demand_schemas import (
    DemandIdInput,
    DemandInput,
    DemandListInput,
    ReferenceInput,
    ReplyDemandInput,
    RespondDemandInput,
)
from app.domains.capacity.extension import request_extension
from app.domains.capacity.extension_schemas import RequestExtensionInput
from app.domains.capacity.schemas import CapacityInput, WorkloadRequestInput
from app.domains.capacity.workload import get_my_workload, request_workload_change
from app.domains.employee_tools import EmployeeTool

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
        "get_demand_basis",
        "查看本部门需求依据和个人负荷，仅限答复方",
        DemandIdInput,
        get_demand_basis,
    ),
    EmployeeTool(
        "reply_demand",
        "代表本部门答复需求并确认承诺日期",
        ReplyDemandInput,
        reply_demand,
        True,
    ),
    EmployeeTool(
        "respond_demand",
        "接受、追问或撤回我方需求",
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
