import type { EmployeeSummary } from "@/client"
import {
  employeeFieldLabels,
  tenureLabel,
} from "@/components/Roster/EmployeeFields"
import { useOrg } from "@/hooks/useOrg"
import { StatusBadge } from "./StatusBadge"

export function publishedLabel(value: EmployeeSummary["latest_published"]) {
  return value
    ? `r${value.revision} · ${new Date(value.published_at).toLocaleDateString("zh-CN")}`
    : "未发布"
}

export function EmployeeSummaryContent({
  employee,
  showHeading = true,
}: {
  employee: EmployeeSummary
  showHeading?: boolean
}) {
  const { org } = useOrg()
  return (
    <div className="flex flex-col gap-6">
      {showHeading && (
        <div>
          <p className="mb-2 text-sm text-muted-foreground">员工画像</p>
          <h1 className="text-3xl font-semibold tracking-tight">
            {employee.display_name}
          </h1>
          <p className="mt-2 text-muted-foreground">
            {employee.title ?? "未填写职位"} ·{" "}
            {employee.department?.name ?? "未分配部门"}
          </p>
        </div>
      )}
      <dl className="grid gap-6 rounded-xl border bg-muted/10 p-6 sm:grid-cols-2 lg:grid-cols-4">
        {(["fill_status", "inference_status", "review_status"] as const).map(
          (kind, i) => (
            <div key={kind}>
              <dt className="mb-2 text-sm text-muted-foreground">
                {["填写进度", "推断进度", "审核状态"][i]}
              </dt>
              <dd>
                <StatusBadge kind={kind} value={employee.progress?.[kind]} />
              </dd>
            </div>
          ),
        )}
        {(org?.employee_fields ?? []).map((field) => (
          <div key={field}>
            <dt className="mb-2 text-sm text-muted-foreground">
              {field === "hire_date" ? "入职年限" : employeeFieldLabels[field]}
            </dt>
            <dd>
              {field === "hire_date" && employee.hire_date
                ? tenureLabel(employee.hire_date)
                : (employee[field] ?? "—")}
            </dd>
          </div>
        ))}
        <div>
          <dt className="mb-2 text-sm text-muted-foreground">最近发布版本</dt>
          <dd>{publishedLabel(employee.latest_published)}</dd>
        </div>
        <div>
          <dt className="mb-2 text-sm text-muted-foreground">任职状态</dt>
          <dd>{employee.employment_status === "active" ? "在职" : "已离职"}</dd>
        </div>
      </dl>
    </div>
  )
}
