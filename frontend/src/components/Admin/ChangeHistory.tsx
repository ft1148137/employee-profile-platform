import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect } from "react"
import { AuditService } from "@/client"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import { useOrg } from "@/hooks/useOrg"
import { actorLabels, auditLabels } from "./auditLabels"

export function ChangeHistory({
  targetType,
  targetIds,
  employeeId,
}: {
  targetType?: string
  targetIds?: string[]
  employeeId?: string
}) {
  const { org, roles } = useOrg()
  const client = useQueryClient()
  const query = useQuery({
    queryKey: ["change-history", org?.id, targetType, targetIds, employeeId],
    enabled: !!org && roles.includes("org_admin"),
    queryFn: async () =>
      (
        await AuditService.readAuditEvents({
          path: { org_id: org!.id },
          query: {
            target_type: targetType,
            target_id: targetIds,
            employee_id: employeeId,
            limit: 20,
          },
        })
      ).data,
  })
  useEffect(
    () =>
      client.getMutationCache().subscribe((event) => {
        if (event.type === "updated" && event.action.type === "success")
          client.invalidateQueries({ queryKey: ["change-history", org?.id] })
      }),
    [client, org?.id],
  )
  if (!org || !roles.includes("org_admin")) return null
  return (
    <section aria-label="修改记录" className="space-y-4 rounded-xl border p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-semibold">修改记录</h2>
        <p className="text-xs text-muted-foreground">最近二十条</p>
      </div>
      <SetupError error={query.error} />
      {query.isError && (
        <Button type="button" variant="outline" onClick={() => query.refetch()}>
          重试
        </Button>
      )}
      {query.isPending && <p role="status">正在加载修改记录…</p>}
      {query.data?.data.length === 0 && (
        <p className="text-sm text-muted-foreground">暂无修改记录</p>
      )}
      <ol className="divide-y">
        {query.data?.data.map((event) => (
          <li
            key={event.id}
            className="flex flex-wrap items-baseline gap-x-4 gap-y-1 py-3 text-sm"
          >
            <time
              dateTime={event.created_at}
              className="text-xs tabular-nums text-muted-foreground"
            >
              {new Date(event.created_at).toLocaleString("zh-CN")}
            </time>
            <span>
              {event.actor?.full_name ||
                event.actor?.username ||
                actorLabels[event.actor_type] ||
                "未知用户"}
            </span>
            <span className="font-medium">
              {auditLabels[event.action] ?? "其他操作"}
            </span>
            <span className="ml-auto text-xs text-muted-foreground">
              {event.details.via === "admin_assistant" ? "管理助手" : "网页"}
            </span>
          </li>
        ))}
      </ol>
    </section>
  )
}
