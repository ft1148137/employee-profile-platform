import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { AdminAssistantService, type ChangeSetPublic } from "@/client"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"

const labels: Record<string, string> = {
  reminder_schedule: "提醒周期",
  stale_after_months: "证据较旧期限",
  require_manager: "需要经理确认",
  category_overrides: "各类能力的确认规则",
  owner_review_days: "本人过目期限（天）",
}
const values: Record<string, string> = {
  quarterly: "每季度",
  yearly: "每年",
  off: "不提醒",
  true: "需要",
  false: "不需要",
}

function display(value: unknown): string {
  if (value && typeof value === "object")
    return (
      Object.entries(value)
        .map(
          ([category, required]) =>
            `${category}：${required ? "需要" : "不需要"}经理确认`,
        )
        .join("；") || "沿用总规则"
    )
  return values[String(value)] ?? String(value)
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
  inline = false,
}: {
  orgId: string
  changeSet: ChangeSetPublic
  disabled: boolean
  inline?: boolean
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
  const confirmAgain =
    current.status === "draft" && Boolean(current.confirmed_once_at)
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
      if (data.id !== changeSet.id && data.status === "draft") setReverse(data)
    },
    onSettled: () => client.invalidateQueries(),
  })
  const busy = disabled || action.isPending || query.isFetching || query.isError
  return (
    <section
      aria-label={inline ? "页面草稿确认" : "设置改动确认卡"}
      className="space-y-4 rounded-xl border border-primary/25 bg-background p-4 shadow-sm"
    >
      <div className="flex items-center justify-between gap-3 border-b pb-3">
        <h3 className="font-semibold">
          {current.undo_of_id ? "撤销前请确认" : "请核对设置改动"}
        </h3>
        <span role="status" className="text-xs text-muted-foreground">
          {confirmAgain ? "请再次确认" : statuses[current.status]}
        </span>
      </div>
      {current.items.map((item, i) => (
        <div key={i} className="space-y-2">
          <p className="text-sm font-medium">
            {item.function === "update_approval_policy"
              ? "经理确认规则"
              : "定期提醒设置"}
          </p>
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
                        : `${display(value)}${field === "stale_after_months" ? " 个月" : ""}`}
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
      {confirmAgain && (
        <div
          role="alert"
          className="space-y-2 rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm"
        >
          <p className="font-semibold">
            请核对影响范围，再次确认后生效。超过五分钟需重新确认。
          </p>
          {current.items
            .filter((item) => item.risk === "high")
            .map((item, i) => (
              <p key={i}>
                影响 {item.impact?.count ?? 0} 人 ·{" "}
                {item.impact?.text ?? "请核对上述改动"}
              </p>
            ))}
        </div>
      )}
      <SetupError error={action.error ?? query.error} />
      {current.status === "draft" && (
        <div className="flex gap-2">
          <Button disabled={busy} onClick={() => action.mutate("apply")}>
            {confirmAgain ? "再次确认" : "确认应用"}
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
