import { useQuery } from "@tanstack/react-query"
import { EmployeeRequestsService } from "@/client"
import { SectionCard } from "@/components/Common/WorkbenchPrimitives"
import { ProfileError } from "@/components/Profile/QueryState"
import useAuth from "@/hooks/useAuth"
import { useOrg } from "@/hooks/useOrg"

const UserInformation = () => {
  const { user } = useAuth()
  const { org, employeeId } = useOrg()
  const reports = useQuery({
    queryKey: ["my-direct-reports", org?.id, employeeId],
    queryFn: async () =>
      (
        await EmployeeRequestsService.readMyDirectReports({
          path: { org_id: org!.id },
        })
      ).data,
    enabled: !!org && !!employeeId,
  })
  return (
    <SectionCard aria-label="个人信息" className="divide-y overflow-hidden p-0">
      <header className="bg-muted/40 px-4 py-2.5">
        <h2 className="text-sm font-semibold">个人信息</h2>
      </header>
      <dl className="divide-y">
        {[
          ["姓名", "与企业员工名单同步。", user?.full_name],
          ["登录邮箱", "如需修改，请联系企业管理员。", user?.email],
        ].map(([label, description, value]) => (
          <div
            key={label}
            className="grid gap-1 px-4 py-2.5 text-sm sm:grid-cols-2 sm:items-center sm:gap-3"
          >
            <dt className="font-medium">
              {label}
              <span className="block text-xs font-normal text-muted-foreground">
                {description}
              </span>
            </dt>
            <dd className="min-w-0 wrap-anywhere sm:text-right">
              {value || "未填写"}
            </dd>
          </div>
        ))}
      </dl>
      {reports.isError && <ProfileError retry={() => reports.refetch()} />}
      {reports.data
        ?.filter((report) => !report.is_reviewer)
        .map((report) => (
          <p
            key={report.employee_code ?? report.employee_name}
            className="px-4 py-2.5 text-sm text-muted-foreground"
          >
            你的直属下属 {report.employee_name}
            {report.employee_code && `（工号：${report.employee_code}）`}{" "}
            的画像更新
            {report.reviewers.length
              ? `由 ${report.reviewers.map((reviewer) => `${reviewer.name}（${reviewer.role}）`).join("、")}审批。`
              : "暂时没有可用的审批人。"}
            如需开通团队管理与审批权限，请联系企业管理员。
          </p>
        ))}
    </SectionCard>
  )
}

export default UserInformation
