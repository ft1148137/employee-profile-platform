import { useQuery } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { BotService } from "@/client"
import { SetupError } from "@/components/Setup/SetupError"

export function useBotConversations(orgId: string | undefined) {
  return useQuery({
    queryKey: ["conversation-history", orgId, false],
    enabled: !!orgId,
    staleTime: 30000,
    queryFn: async () =>
      (await BotService.listSessions({ query: { org_id: orgId! } })).data,
  })
}

export function SessionHistory({
  orgId,
  currentId,
  explicitMode,
  onSelect,
}: {
  orgId: string
  currentId?: string | null
  explicitMode: boolean
  onSelect?: () => void
}) {
  const navigate = useNavigate()
  const history = useBotConversations(orgId)
  return (
    <section
      aria-label="历史对话"
      className="min-h-0 flex-1 overflow-y-auto text-sm"
    >
      {history.isPending && <p role="status">正在加载对话…</p>}
      <SetupError error={history.error} />
      {history.data?.data.map((entry) => (
        <button
          key={entry.id}
          type="button"
          aria-current={entry.id === currentId ? "true" : undefined}
          className="block w-full rounded-md px-2 py-2 text-left hover:bg-sidebar-accent aria-current:bg-sidebar-accent"
          onClick={() => {
            navigate({
              to: "/assistant",
              search: {
                mode: explicitMode ? "personal" : undefined,
                session: entry.id,
              },
            })
            onSelect?.()
          }}
        >
          <span className="block truncate">{entry.preview || "对话"}</span>
          <span className="block text-xs text-muted-foreground">
            最近活动：{new Date(entry.last_activity_at).toLocaleString("zh-CN")}
          </span>
        </button>
      ))}
      {history.data?.data.length === 0 && (
        <p className="px-2 py-2 text-muted-foreground">还没有历史对话</p>
      )}
      {history.data && (
        <p className="px-2 py-3 text-xs text-muted-foreground">
          {history.data.retention_days === 0
            ? "对话在 2 小时没有活动后会被清除，管理员可以在内置机器人设置里改保留天数"
            : `保留 ${history.data.retention_days} 天`}
        </p>
      )}
    </section>
  )
}
