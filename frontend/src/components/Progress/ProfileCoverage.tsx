import { useQuery } from "@tanstack/react-query"
import { ChevronDown } from "lucide-react"
import { useState } from "react"
import {
  type CoverageSummary,
  OrgsService,
  type ProgressReviewStatus,
} from "@/client"
import { HelpTip, SectionCard } from "@/components/Common/WorkbenchPrimitives"
import { Button } from "@/components/ui/button"

const statuses = [
  ["published", "已发布", "bg-emerald-500"],
  ["disputed", "存在争议", "bg-rose-500"],
  ["pending_employee", "待本人确认", "bg-amber-500"],
  ["pending_manager", "待审批", "bg-sky-500"],
  ["none", "未知", "bg-muted-foreground"],
] as const

function counts(summary: CoverageSummary) {
  return statuses.map(([status, title, color]) => ({
    status,
    title,
    color,
    value: summary.statuses.find((item) => item.status === status),
  }))
}

export function ProfileCoverage({
  orgId,
  department,
  onDepartmentChange,
  reviewStatus,
  onStatusChange,
}: {
  orgId: string
  department: string
  onDepartmentChange?: (id: string) => void
  reviewStatus: ProgressReviewStatus | null
  onStatusChange?: (status: ProgressReviewStatus | null) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const coverage = useQuery({
    queryKey: ["coverage", orgId],
    queryFn: async () =>
      (await OrgsService.getCoverage({ path: { org_id: orgId } })).data,
  })
  const summary = coverage.data
  const metrics = summary ? counts(summary) : []
  return (
    <SectionCard aria-label="画像覆盖率" className="space-y-2 px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <div className="flex items-center gap-1.5">
          <h2 className="whitespace-nowrap font-semibold text-sm">
            画像覆盖率
          </h2>
          <HelpTip
            label="画像覆盖率说明"
            content="未评估不代表不具备该能力。点击状态可筛选对应的在职员工，再次点击可取消筛选。"
          />
        </div>
        {coverage.isPending ? (
          <p role="status" className="text-sm">
            正在加载覆盖率…
          </p>
        ) : coverage.isError ? (
          <div role="alert" className="flex items-center gap-2 text-sm">
            无法加载画像覆盖率。
            <Button
              variant="outline"
              size="sm"
              onClick={() => coverage.refetch()}
            >
              重试
            </Button>
          </div>
        ) : (
          summary && (
            <>
              <p className="whitespace-nowrap text-muted-foreground text-xs">
                范围内在职员工{" "}
                <strong className="text-foreground text-sm tabular-nums">
                  {summary.total}
                </strong>{" "}
                人
              </p>
              {metrics.map(({ status, title, color, value }) => (
                <Button
                  key={status}
                  variant={reviewStatus === status ? "secondary" : "ghost"}
                  size="sm"
                  className="h-7 px-1.5 text-xs tabular-nums"
                  disabled={!onStatusChange}
                  aria-pressed={reviewStatus === status}
                  onClick={() =>
                    onStatusChange?.(reviewStatus === status ? null : status)
                  }
                >
                  <span className={`size-1.5 shrink-0 rounded-full ${color}`} />
                  <span>{title}</span>
                  <strong>{value?.count ?? 0}</strong>
                  <span className="text-muted-foreground">
                    {value?.percentage ?? 0}%
                  </span>
                </Button>
              ))}
              <Button
                variant="ghost"
                size="sm"
                aria-expanded={expanded}
                className="ml-auto h-7 text-xs"
                onClick={() => setExpanded(!expanded)}
              >
                按部门分布
                <ChevronDown
                  aria-hidden="true"
                  className={expanded ? "rotate-180" : ""}
                />
              </Button>
            </>
          )
        )}
      </div>
      {summary && (
        <>
          <div
            aria-hidden="true"
            className="flex h-1.5 overflow-hidden rounded-full bg-muted"
          >
            {metrics.map(({ status, color, value }) => (
              <span
                key={status}
                className={color}
                style={{ width: `${value?.percentage ?? 0}%` }}
              />
            ))}
          </div>
          {(expanded ||
            summary.overdue_initial_requests > 0 ||
            summary.review_backlog_requests > 0 ||
            summary.jira_sync_failure) && (
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-muted-foreground text-xs">
              <p>初始画像超期未确认：{summary.overdue_initial_requests} 人</p>
              <p>等待审批超过 7 天：{summary.review_backlog_requests} 个请求</p>
              {summary.jira_sync_failure && (
                <p className="text-destructive">
                  {summary.jira_sync_failure.connection_name} 最近一次同步失败：
                  {new Date(summary.jira_sync_failure.failed_at).toLocaleString(
                    "zh-CN",
                  )}
                </p>
              )}
            </div>
          )}
          {expanded && (
            <div className="divide-y border-surface-border border-t">
              <div className="flex flex-wrap items-center justify-between gap-2 py-2">
                <p className="text-muted-foreground text-xs">
                  未分配部门的员工计入总人数。
                </p>
                <Button
                  variant="ghost"
                  size="sm"
                  aria-pressed={department === "all"}
                  disabled={!onDepartmentChange}
                  onClick={() => onDepartmentChange?.("all")}
                >
                  全部部门
                </Button>
              </div>
              {summary.departments.map((item) => (
                <div key={item.department_id} className="space-y-1.5 py-2.5">
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                    <Button
                      variant={
                        department === item.department_id
                          ? "secondary"
                          : "ghost"
                      }
                      size="sm"
                      aria-pressed={department === item.department_id}
                      disabled={!onDepartmentChange}
                      onClick={() => onDepartmentChange?.(item.department_id)}
                    >
                      {item.department_name} · {item.total} 人
                    </Button>
                    <dl className="flex flex-wrap gap-x-3 gap-y-1 text-xs tabular-nums">
                      {counts(item).map(({ status, title, value }) => (
                        <div key={status} className="flex gap-1">
                          <dt>{title}</dt>
                          <dd className="text-muted-foreground">
                            {value?.count ?? 0} · {value?.percentage ?? 0}%
                          </dd>
                        </div>
                      ))}
                    </dl>
                  </div>
                  {item.with_descendants && (
                    <p className="px-3 text-muted-foreground text-xs">
                      含下级部门：{item.with_descendants.total} 人
                      {counts(item.with_descendants)
                        .map(
                          ({ title, value }) =>
                            ` · ${title} ${value?.count ?? 0}`,
                        )
                        .join("")}
                    </p>
                  )}
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </SectionCard>
  )
}
