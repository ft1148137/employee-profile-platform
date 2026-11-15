from app.domains.capacity.schemas import CapacityInput, WorkloadRequestInput
from app.domains.capacity.workload import get_my_workload, request_workload_change
from app.domains.employee_tools import EmployeeTool

CAPACITY_TOOLS = (
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
