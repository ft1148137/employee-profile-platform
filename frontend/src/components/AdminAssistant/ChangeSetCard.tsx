import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { AdminAssistantService, type ChangeSetPublic } from "@/client"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"

const labels: Record<string, string> = {
  reminder_schedule: "提醒周期",
  stale_after_months: "证据较旧期限",
}
const values: Record<string, string> = {
  quarterly: "每季度",
  yearly: "每年",
  off: "不提醒",
}
const statuses = {
  draft: "待确认",
  applied: "已应用",
  discarded: "已放弃",
  stale: "内容已变化，请重新预览",
  undone: "已撤销",
}

export function ChangeSetCard({
  orgId,
  changeSet,
  disabled,
}: {
  orgId: string
  changeSet: ChangeSetPublic
  disabled: boolean
}) {
  const client = useQueryClient()
  const [reverse, setReverse] = useState<ChangeSetPublic | null>(null)
  const path = { org_id: orgId, id: changeSet.id }
  const key = ["admin-change-set", orgId, changeSet.id]
  const query = useQuery({
    queryKey: key,
    initialData: changeSet,
    queryFn: async () =>
      (await AdminAssistantService.assistantGetChangeSet({ path })).data,
  })
  const current = query.data
  const action = useMutation({
    mutationFn: async (kind: "apply" | "discard" | "undo") => {
      if (kind === "apply")
        return (
          await AdminAssistantService.assistantApplyChangeSet({
            path,
            body: { revision: current.revision },
          })
        ).data
      if (kind === "discard")
        return (await AdminAssistantService.assistantDiscardChangeSet({ path }))
          .data
      return (await AdminAssistantService.assistantUndoChangeSet({ path })).data
    },
    onSuccess: (data) => {
      client.setQueryData(["admin-change-set", orgId, data.id], data)
      if (data.status === "applied") {
        client.invalidateQueries({ queryKey: ["update-settings", orgId] })
        client.invalidateQueries({ queryKey: ["profile", orgId] })
      }
      if (data.id !== changeSet.id && data.status === "draft") setReverse(data)
    },
    onSettled: () =>
      client.invalidateQueries({ queryKey: ["admin-change-set", orgId] }),
  })
  const busy = disabled || action.isPending || query.isFetching || query.isError
  return (
    <section
      aria-label="设置改动确认卡"
      className="space-y-4 rounded-xl border border-primary/25 bg-background p-4 shadow-sm"
    >
      <div className="flex items-center justify-between gap-3 border-b pb-3">
        <h3 className="font-semibold">
          {current.undo_of_id ? "撤销前请确认" : "请核对设置改动"}
        </h3>
        <span role="status" className="text-xs text-muted-foreground">
          {statuses[current.status]}
        </span>
      </div>
      {current.items.map((item, i) => (
        <div key={i} className="space-y-2">
          <p className="text-sm font-medium">定期提醒设置</p>
          {Object.keys(item.after).map((field) => (
            <div key={field} className="rounded-lg bg-muted/40 p-3 text-sm">
              <p className="mb-2 text-muted-foreground">
                {labels[field] ?? "设置项"}
              </p>
              <div className="grid grid-cols-2 gap-3">
                {[
                  { title: "修改前", value: item.before?.[field] },
                  { title: "修改后", value: item.after[field] },
                ].map(({ title, value }) => (
                  <div key={title}>
                    <p className="text-xs text-muted-foreground">{title}</p>
                    <p
                      className={
                        title === "修改后" ? "font-semibold text-primary" : ""
                      }
                    >
                      {value == null
                        ? "未设置"
                        : (values[String(value)] ??
                          `${value}${field === "stale_after_months" ? " 个月" : ""}`)}
                    </p>
                  </div>
                ))}
              </div>
            </div>
          ))}
          {item.impact && (
            <p className="text-xs text-muted-foreground">
              影响 {item.impact.count} 人 · {item.impact.text}
            </p>
          )}
        </div>
      ))}
      <SetupError error={action.error ?? query.error} />
      {current.status === "draft" && (
        <div className="flex gap-2">
          <Button disabled={busy} onClick={() => action.mutate("apply")}>
            确认应用
          </Button>
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => action.mutate("discard")}
          >
            放弃
          </Button>
        </div>
      )}
      {current.status === "applied" &&
        current.items.every((item) => item.before !== null) && (
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => action.mutate("undo")}
          >
            撤销
          </Button>
        )}
      {reverse && (
        <ChangeSetCard orgId={orgId} changeSet={reverse} disabled={disabled} />
      )}
    </section>
  )
}
