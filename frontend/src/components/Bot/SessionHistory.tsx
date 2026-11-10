import { useQuery } from "@tanstack/react-query"
import { useState } from "react"
import { AdminAssistantService, BotService } from "@/client"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"

export function SessionHistory({
  orgId,
  administrator = false,
  currentId,
  disabled,
  onSelect,
  onNew,
}: {
  orgId: string
  administrator?: boolean
  currentId: string
  disabled: boolean
  onSelect: (id: string) => void
  onNew: () => void
}) {
  const [open, setOpen] = useState(false)
  const history = useQuery({
    queryKey: ["conversation-history", orgId, administrator],
    enabled: open,
    queryFn: async () =>
      (
        await (administrator
          ? AdminAssistantService.assistantListSessions
          : BotService.listSessions)({ query: { org_id: orgId } })
      ).data,
  })
  return (
    <div className="shrink-0 space-y-3 border-b p-4">
      <div className="flex gap-2">
        <Button variant="outline" disabled={disabled} onClick={onNew}>
          新对话
        </Button>
        <Button
          variant="outline"
          disabled={disabled}
          aria-expanded={open}
          onClick={() => {
            setOpen(!open)
            if (!open) history.refetch()
          }}
        >
          历史对话
        </Button>
      </div>
      {open && (
        <section aria-label="历史对话" className="space-y-2 text-sm">
          {history.isPending && <p role="status">正在加载对话…</p>}
          <SetupError error={history.error} />
          <div className="max-h-48 space-y-2 overflow-y-auto">
            {history.data?.data.map((entry) => (
              <button
                key={entry.id}
                type="button"
                disabled={disabled}
                aria-current={entry.id === currentId ? "true" : undefined}
                className="w-full rounded-lg border p-3 text-left hover:bg-muted aria-current:bg-muted"
                onClick={() => {
                  onSelect(entry.id)
                  setOpen(false)
                }}
              >
                <span className="block break-words">
                  {entry.preview || "对话"}
                </span>
                <span className="block text-xs text-muted-foreground">
                  开始：{new Date(entry.started_at).toLocaleString("zh-CN")}
                  <br />
                  最近活动：
                  {new Date(entry.last_activity_at).toLocaleString("zh-CN")}
                </span>
              </button>
            ))}
          </div>
          {history.data?.data.length === 0 && <p>还没有历史对话</p>}
          {history.data && (
            <p className="text-xs text-muted-foreground">
              {history.data.retention_days === 0
                ? "对话在 2 小时没有活动后会被清除，管理员可以在内置机器人设置里改保留天数"
                : `保留 ${history.data.retention_days} 天`}
            </p>
          )}
        </section>
      )}
    </div>
  )
}
