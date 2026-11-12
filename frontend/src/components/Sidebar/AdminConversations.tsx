import { useQuery } from "@tanstack/react-query"
import { Link, useLocation, useNavigate } from "@tanstack/react-router"
import { Bot, ListChecks, Plus } from "lucide-react"
import { AdminAssistantService } from "@/client"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import { rememberConversation } from "@/hooks/useCurrentConversation"
import { useSetup } from "@/hooks/useSetup"

export function useAdminConversations(orgId: string | undefined) {
  return useQuery({
    queryKey: ["conversation-history", orgId, true],
    enabled: !!orgId,
    staleTime: 30000,
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
  storageKey,
  onSelect,
}: {
  orgId: string
  storageKey: string
  onSelect?: () => void
}) {
  const navigate = useNavigate()
  const location = useLocation()
  const selected = new URLSearchParams(location.searchStr).get("session")
  const setup = useSetup()
  const remaining =
    setup.data?.steps.filter((step) => step.status === "pending").length ?? 0
  const history = useAdminConversations(orgId)
  const entries = history.data?.data ?? []
  return (
    <section
      aria-label="管理助手会话"
      className="flex h-full min-h-0 flex-col bg-sidebar/40"
    >
      <header className="flex h-16 shrink-0 items-center justify-between border-b px-5">
        <h2 className="font-semibold">聊天</h2>
        <Button
          variant="ghost"
          size="icon"
          aria-label="新对话"
          onClick={() => {
            rememberConversation(storageKey, "")
            navigate({ to: "/admin/assistant", search: {} })
            onSelect?.()
          }}
        >
          <Plus className="size-4" />
        </Button>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto p-2">
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
              to="/admin/assistant"
              search={{ session: entry.id }}
              aria-label={
                onboarding ? `首次设置 · ${remaining} 步待完成` : title
              }
              aria-current={selected === entry.id ? "page" : undefined}
              onClick={onSelect}
              className="flex min-w-0 items-center gap-3 rounded-xl px-3 py-3 hover:bg-sidebar-accent aria-current:bg-sidebar-accent"
              title={title}
            >
              <span className="flex size-9 shrink-0 items-center justify-center rounded-lg border bg-background">
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
          <p className="p-3 text-sm text-muted-foreground">
            还没有对话，开始一个新任务吧。
          </p>
        )}
        {history.data && (
          <p className="px-3 py-4 text-xs text-muted-foreground">
            {history.data.retention_days
              ? `保留 ${history.data.retention_days} 天`
              : "2 小时没有活动后清除"}
          </p>
        )}
      </div>
    </section>
  )
}
