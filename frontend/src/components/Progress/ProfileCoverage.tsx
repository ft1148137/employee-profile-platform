import { useQuery } from "@tanstack/react-query"
import { type CoveragePublic, OrgsService } from "@/client"
import { Button } from "@/components/ui/button"

const statuses = [
  ["published", "已发布", "bg-emerald-500"],
  ["disputed", "存在争议", "bg-rose-500"],
  ["pending_employee", "待本人确认", "bg-amber-500"],
  ["pending_manager", "待审批", "bg-sky-500"],
  ["none", "未知", "bg-muted-foreground"],
] as const

function Counts({ summary }: { summary: Pick<CoveragePublic, "statuses"> }) {
  return statuses.map(([status, title]) => {
    const value = summary.statuses.find((item) => item.status === status)
    return (
      <div key={status} className="space-y-1">
        <dt className="text-xs text-muted-foreground">{title}</dt>
        <dd className="tabular-nums">
          <span className="text-lg font-semibold">{value?.count ?? 0}</span>
          <span className="ml-2 text-xs text-muted-foreground">
            {value?.percentage ?? 0}%
          </span>
        </dd>
      </div>
    )
  })
}

export function ProfileCoverage({
  orgId,
  department,
  onDepartmentChange,
}: {
  orgId: string
  department: string
  onDepartmentChange: (id: string) => void
}) {
  const coverage = useQuery({
    queryKey: ["coverage", orgId],
    queryFn: async () =>
      (await OrgsService.getCoverage({ path: { org_id: orgId } })).data,
  })
  return (
    <section aria-label="画像覆盖率" className="rounded-xl border">
      <div className="flex flex-wrap items-baseline justify-between gap-2 border-b p-4">
        <h2 className="font-semibold">画像覆盖率</h2>
        <p className="text-xs text-muted-foreground">
          统计在职员工（未评估不代表不具备该能力）
        </p>
      </div>
      {coverage.isPending ? (
        <p role="status" className="p-4">
          正在加载覆盖率…
        </p>
      ) : coverage.isError ? (
        <div role="alert" className="p-4">
          无法加载画像覆盖率。
          <Button variant="outline" onClick={() => coverage.refetch()}>
            重试
          </Button>
        </div>
      ) : (
        <div className="space-y-4 p-4">
          <p className="text-sm">范围内在职员工 {coverage.data.total} 人</p>
          <dl className="grid grid-cols-2 gap-4 sm:grid-cols-5">
            <Counts summary={coverage.data} />
          </dl>
          <div
            aria-hidden="true"
            className="flex h-2 overflow-hidden rounded-full bg-muted"
          >
            {statuses.map(([status, , color]) => (
              <span
                key={status}
                className={color}
                style={{
                  width: `${coverage.data.statuses.find((s) => s.status === status)?.percentage ?? 0}%`,
                }}
              />
            ))}
          </div>
          <div className="flex flex-wrap gap-x-6 gap-y-2 border-t pt-3 text-sm">
            <p>
              初始画像超期未确认：{coverage.data.overdue_initial_requests} 人
            </p>
            <p>
              等待审批超过 7 天：{coverage.data.review_backlog_requests} 个请求
            </p>
            {coverage.data.jira_sync_failure && (
              <p className="text-destructive">
                {coverage.data.jira_sync_failure.connection_name}{" "}
                最近一次同步失败：
                {new Date(
                  coverage.data.jira_sync_failure.failed_at,
                ).toLocaleString("zh-CN")}
              </p>
            )}
          </div>
          <details>
            <summary className="cursor-pointer text-sm font-medium">
              按部门分布
            </summary>
            <p className="mt-2 text-xs text-muted-foreground">
              未分配部门的员工计入总人数。
            </p>
            <Button
              variant={department === "all" ? "secondary" : "ghost"}
              className="my-2"
              onClick={() => onDepartmentChange("all")}
            >
              全部部门
            </Button>
            <div className="divide-y">
              {coverage.data.departments.map((item) => (
                <div key={item.department_id} className="space-y-2 py-3">
                  <Button
                    variant={
                      department === item.department_id ? "secondary" : "ghost"
                    }
                    aria-pressed={department === item.department_id}
                    onClick={() => onDepartmentChange(item.department_id)}
                  >
                    {item.department_name} · {item.total} 人
                  </Button>
                  <dl className="grid grid-cols-2 gap-3 sm:grid-cols-5">
                    <Counts summary={item} />
                  </dl>
                  {item.with_descendants && (
                    <p className="text-sm text-muted-foreground">
                      含下级部门：{item.with_descendants.total} 人
                      {statuses
                        .map(
                          ([status, title]) =>
                            ` · ${title} ${item.with_descendants?.statuses.find((s) => s.status === status)?.count ?? 0}`,
                        )
                        .join("")}
                    </p>
                  )}
                </div>
              ))}
            </div>
          </details>
        </div>
      )}
    </section>
  )
}
