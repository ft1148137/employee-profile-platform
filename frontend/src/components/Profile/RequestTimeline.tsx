import { useQuery } from "@tanstack/react-query"
import { useState } from "react"
import { ChangeRequestsService } from "@/client"
import { ProfileError, ProfileLoading } from "./QueryState"

export const viaLabels: Record<string, string> = {
  web: "网页",
  mcp: "MCP",
  import: "导入",
  bot: "机器人",
  admin_handover: "管理员送审",
  im: "IM",
  policy: "审批规则自动发布",
  sync: "增量同步自动补充证据",
}
export const decisionLabels = {
  approved: "批准",
  changes_requested: "退回修改",
  rejected: "拒绝",
}
const actions: Record<string, string> = {
  created: "创建请求",
  submitted: "提交审批",
  decided: "审批决定",
  published: "发布画像",
  withdrawn: "撤回请求",
  returned: "退回生成新草稿",
}

export function RequestTimeline({
  orgId,
  employeeId,
  requestId,
}: {
  orgId: string
  employeeId: string
  requestId: string
}) {
  const [open, setOpen] = useState(false)
  const query = useQuery({
    enabled: open,
    queryKey: ["request-timeline", orgId, requestId],
    queryFn: async () =>
      (
        await ChangeRequestsService.readRequestTimeline({
          path: {
            org_id: orgId,
            employee_id: employeeId,
            request_id: requestId,
          },
        })
      ).data,
  })
  return (
    <details onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary className="cursor-pointer py-2 text-primary">请求时间线</summary>
      {query.isPending ? (
        <ProfileLoading />
      ) : query.isError ? (
        <ProfileError retry={() => query.refetch()} />
      ) : (
        <ol
          aria-label="请求时间线"
          className="ml-2 space-y-4 border-l pl-5 text-sm"
        >
          {query.data.map((event, index) => (
            <li key={`${event.action}-${index}`} className="space-y-1">
              <p>
                {actions[event.action] ?? "请求更新"}
                {event.version && ` · r${event.version}`}
                {event.reviewer && ` · ${event.reviewer}`}
                {event.via && ` · ${viaLabels[event.via] ?? "其他入口"}`}
                {event.client_name && ` · ${event.client_name}`}
              </p>
              <time className="text-xs text-muted-foreground">
                {new Date(event.at).toLocaleString("zh-CN")}
              </time>
              {event.request_id && (
                <a
                  className="block text-primary underline"
                  href={`#request-${event.request_id}`}
                >
                  查看退回草稿
                </a>
              )}
            </li>
          ))}
        </ol>
      )}
    </details>
  )
}
