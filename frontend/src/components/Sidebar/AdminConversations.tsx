import { useQuery } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import { Bot, ListChecks } from "lucide-react"
import { AdminAssistantService } from "@/client"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import { useSetup } from "@/hooks/useSetup"

export function useAdminConversations(
  orgId: string | undefined,
  staleTime = 30_000,
) {
  return useQuery({
    queryKey: ["conversation-history", orgId, true],
    enabled: !!orgId,
    staleTime,
    queryFn: async () =>
      (
        await AdminAssistantService.assistantListSessions({
          query: { org_id: orgId! },
        })
      ).data,
  })
}

export function AdminConversations({
  orgId,
  selected,
  onSelect,
}: {
  orgId: string
  selected?: string
  onSelect?: () => void
}) {
  const setup = useSetup()
  const remaining =
    setup.data?.steps.filter((step) => step.status === "pending").length ?? 0
  const history = useAdminConversations(orgId)
  const entries = history.data?.data ?? []
  return (
    <section
      aria-label="管理助手会话"
      className="min-h-0 flex-1 overflow-y-auto"
    >
      {history.isPending && (
        <p role="status" className="p-3 text-sm">
          正在加载对话…
        </p>
      )}
      <SetupError error={history.error} />
      {history.isError && (
        <Button variant="ghost" onClick={() => history.refetch()}>
          重试加载对话
        </Button>
      )}
      {entries.map((entry) => {
        const onboarding = entry.purpose === "setup" && !setup.data?.completed
        const title = onboarding ? "首次设置" : entry.preview || "对话"
        const Icon = onboarding ? ListChecks : Bot
        const activity = new Date(entry.last_activity_at)
        const today = activity.toDateString() === new Date().toDateString()
        return (
          <Link
            key={entry.id}
            to="/assistant"
            search={{ mode: "admin", session: entry.id }}
            aria-label={onboarding ? `首次设置 · ${remaining} 步待完成` : title}
            aria-current={selected === entry.id ? "page" : undefined}
            onClick={onSelect}
            className="flex min-w-0 items-center gap-2 rounded-md px-2 py-2 hover:bg-sidebar-accent aria-current:bg-sidebar-accent"
            title={title}
          >
            <span className="flex size-7 shrink-0 items-center justify-center rounded-lg border bg-background">
              <Icon className="size-4 text-muted-foreground" />
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="min-w-0 flex-1 truncate text-sm font-medium">
                  {title}
                </span>
                <time
                  dateTime={entry.last_activity_at}
                  className="shrink-0 text-[11px] tabular-nums text-muted-foreground"
                >
                  {today
                    ? activity.toLocaleTimeString("zh-CN", {
                        hour: "2-digit",
                        minute: "2-digit",
                        hour12: false,
                      })
                    : `${activity.getMonth() + 1}/${activity.getDate()}`}
                </time>
              </div>
              <p className="mt-1 text-xs text-muted-foreground">
                {onboarding
                  ? `${remaining} 步待完成`
                  : `管理助手 · ${entry.turn_count} 轮`}
              </p>
            </div>
          </Link>
        )
      })}
      {history.data && entries.length === 0 && (
        <p className="p-3 text-sm text-muted-foreground">暂无历史对话</p>
      )}
      {history.data && (
        <p className="px-3 py-4 text-xs text-muted-foreground">
          {history.data.retention_days
            ? `保留 ${history.data.retention_days} 天`
            : "闲置 2 小时后自动清除"}
        </p>
      )}
    </section>
  )
}
