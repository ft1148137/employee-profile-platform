import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import { AuditService } from "@/client"
import {
  SectionCard,
  SideDrawer,
} from "@/components/Common/WorkbenchPrimitives"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import { useOrg } from "@/hooks/useOrg"
import { cn } from "@/lib/utils"
import { actorLabels, auditLabels } from "./auditLabels"

export function ChangeHistory({
  targetType,
  targetIds,
  employeeId,
  variant = "card",
}: {
  targetType?: string
  targetIds?: string[]
  employeeId?: string
  variant?: "card" | "compact" | "drawer"
}) {
  const { org, roles } = useOrg()
  const client = useQueryClient()
  const [open, setOpen] = useState(false)
  const canRead = !!org && roles.includes("org_admin")
  const query = useQuery({
    queryKey: ["change-history", org?.id, targetType, targetIds, employeeId],
    enabled: canRead && (variant !== "drawer" || open),
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
  if (!canRead) return null
  const compact = variant !== "card"
  const history = (
    <>
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
            className={cn(
              "flex flex-wrap items-baseline gap-y-1 text-sm",
              compact ? "gap-x-2 py-2" : "gap-x-4 py-3",
            )}
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
    </>
  )
  if (variant === "drawer") {
    return (
      <SideDrawer
        title="变更历史"
        trigger={
          <Button type="button" variant="outline" size="sm">
            变更历史
          </Button>
        }
        open={open}
        onOpenChange={setOpen}
      >
        <div className="space-y-2">
          <p className="text-xs text-muted-foreground">最近 20 条</p>
          {history}
        </div>
      </SideDrawer>
    )
  }
  return (
    <SectionCard
      aria-label="修改记录"
      className={compact ? "space-y-2 p-3" : "space-y-4 rounded-xl p-5"}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className={cn("font-semibold", compact ? "text-sm" : "text-lg")}>
          修改记录
        </h2>
        <p className="text-xs text-muted-foreground">最近 20 条</p>
      </div>
      {history}
    </SectionCard>
  )
}
