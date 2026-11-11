import { useQuery } from "@tanstack/react-query"
import { Link, useLocation } from "@tanstack/react-router"
import { useState } from "react"
import { AdminAssistantService } from "@/client"
import { SetupError } from "@/components/Setup/SetupError"
import { useSidebar } from "@/components/ui/sidebar"
import { useCurrentConversation } from "@/hooks/useCurrentConversation"

export function AdminConversations({
  orgId,
  storageKey,
}: {
  orgId: string
  storageKey: string
}) {
  const [expanded, setExpanded] = useState(false)
  const remembered = useCurrentConversation(storageKey)
  const location = useLocation()
  const selected =
    location.pathname === "/admin/assistant"
      ? new URLSearchParams(location.searchStr).get("session")
      : remembered
  const { isMobile, setOpenMobile } = useSidebar()
  const history = useQuery({
    queryKey: ["conversation-history", orgId, true],
    staleTime: 30000,
    queryFn: async () =>
      (
        await AdminAssistantService.assistantListSessions({
          query: { org_id: orgId },
        })
      ).data,
  })
  const entries = history.data?.data ?? []
  return (
    <section
      aria-label="管理助手会话"
      className="min-h-0 flex-1 overflow-y-auto px-2 group-data-[collapsible=icon]:hidden"
    >
      <p className="px-2 py-1 text-xs text-muted-foreground">最近对话</p>
      {history.isPending && (
        <p role="status" className="p-2 text-xs">
          正在加载对话…
        </p>
      )}
      <SetupError error={history.error} />
      {history.isError && (
        <button type="button" onClick={() => history.refetch()}>
          重试加载对话
        </button>
      )}
      {entries.slice(0, expanded ? entries.length : 10).map((entry) => (
        <Link
          key={entry.id}
          to="/admin/assistant"
          search={{ session: entry.id }}
          aria-current={selected === entry.id ? "true" : undefined}
          onClick={() => {
            if (isMobile) setOpenMobile(false)
          }}
          className="block truncate rounded-md px-2 py-1.5 text-sm hover:bg-sidebar-accent aria-current:bg-sidebar-accent"
          title={`${entry.preview || "对话"} · 最近活动：${new Date(entry.last_activity_at).toLocaleString("zh-CN")}`}
        >
          {entry.preview || "对话"}
        </Link>
      ))}
      {entries.length > 10 && (
        <button
          type="button"
          aria-expanded={expanded}
          className="w-full rounded-md px-2 py-2 text-left text-xs text-muted-foreground hover:bg-sidebar-accent"
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? "收起更早的对话" : `更早的 ${entries.length - 10} 个对话`}
        </button>
      )}
      {history.data && entries.length === 0 && (
        <p className="p-2 text-xs text-muted-foreground">还没有历史对话</p>
      )}
      {history.data && (
        <p className="px-2 py-2 text-xs text-muted-foreground">
          {history.data.retention_days === 0
            ? "对话在 2 小时没有活动后会被清除"
            : `保留 ${history.data.retention_days} 天`}
        </p>
      )}
    </section>
  )
}
