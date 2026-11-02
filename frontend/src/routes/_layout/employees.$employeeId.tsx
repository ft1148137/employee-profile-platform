import { useQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { isAxiosError } from "axios"
import { EmployeesService } from "@/client"
import { EmployeeSummaryContent } from "@/components/Profile/EmployeeSummary"
import { OrgState } from "@/components/Profile/OrgState"
import { Button } from "@/components/ui/button"
import { useOrg } from "@/hooks/useOrg"

export const Route = createFileRoute("/_layout/employees/$employeeId")({
  component: EmployeePage,
  head: () => ({ meta: [{ title: "员工画像 - 员工画像平台" }] }),
})

function EmployeePage() {
  const { org } = useOrg()
  const { employeeId } = Route.useParams()
  const query = useQuery({
    queryKey: ["employee", org?.id, employeeId],
    queryFn: async () =>
      (
        await EmployeesService.readEmployee({
          path: { org_id: org!.id, employee_id: employeeId },
        })
      ).data,
    enabled: !!org,
    retry: false,
  })
  if (!org) return <OrgState />
  if (query.isPending) return <p role="status">正在加载员工…</p>
  if (query.isError)
    return (
      <div role="alert">
        {isAxiosError(query.error) && query.error.response?.status === 404
          ? "未找到该员工或你没有权限查看"
          : "无法加载员工信息。"}
        <Button variant="outline" onClick={() => query.refetch()}>
          重试
        </Button>
      </div>
    )
  return <EmployeeSummaryContent employee={query.data} />
}
