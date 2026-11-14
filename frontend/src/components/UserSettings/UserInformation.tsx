import { useQuery } from "@tanstack/react-query"
import { EmployeeRequestsService } from "@/client"
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
    <section className="max-w-md space-y-4 py-4">
      <h2 className="text-lg font-semibold">个人信息</h2>
      <p className="text-sm text-muted-foreground">
        姓名和邮箱由企业管理员按员工名单维护。
      </p>
      <dl className="space-y-4">
        {[
          ["姓名", user?.full_name],
          ["登录名", user?.username],
          ["邮箱", user?.email],
        ].map(([label, value]) => (
          <div key={label}>
            <dt className="text-sm text-muted-foreground">{label}</dt>
            <dd className="py-2 break-all">{value || "未填写"}</dd>
          </div>
        ))}
      </dl>
      {reports.isError && <ProfileError retry={() => reports.refetch()} />}
      {reports.data
        ?.filter((report) => !report.is_reviewer)
        .map((report) => (
          <p
            key={report.employee_code ?? report.employee_name}
            className="text-sm text-muted-foreground"
          >
            你的直属下属 {report.employee_name}
            {report.employee_code && `（工号：${report.employee_code}）`}{" "}
            的画像请求
            {report.reviewers.length
              ? `由 ${report.reviewers.map((reviewer) => `${reviewer.name}（${reviewer.role}）`).join("、")}审批。`
              : "暂时没有可用的审批人。"}
            你没有团队查看和审批权限，需要的话请找企业管理员。
          </p>
        ))}
    </section>
  )
}

export default UserInformation
