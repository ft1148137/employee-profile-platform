from app.domains.capacity.demand import (
    capacity_reference,
    get_demand,
    list_capacity_departments,
    list_demands,
    submit_demand,
)
from app.domains.capacity.demand_schemas import (
    DemandIdInput,
    DemandInput,
    DemandListInput,
    ReferenceInput,
)
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
        "get_my_workload",
        "查看我的 Jira 参考值和经理确认负荷",
        CapacityInput,
        get_my_workload,
    ),
    EmployeeTool(
        "request_workload_change",
        "申请调整本人的负荷，等待经理审批",
        WorkloadRequestInput,
        request_workload_change,
        True,
    ),
)
