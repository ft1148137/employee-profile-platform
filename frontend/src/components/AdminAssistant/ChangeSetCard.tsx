import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import {
  AdminAssistantService,
  type ChangeSetApplied,
  type ChangeSetPublic,
} from "@/client"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import { useCopyToClipboard } from "@/hooks/useCopyToClipboard"

const labels: Record<string, string> = {
  platform: "沟通渠道",
  email_auto_bind: "按企业邮箱自动绑定",
  allowed_clients: "允许的客户端",
  enabled: "启用状态",
  kind: "模型类型",
  provider: "模型服务商",
  model: "模型名",
  reasoning_effort: "推理强度",
  question_guide: "提问指南",
  retention_days: "聊天原文保留天数",
  reminder_schedule: "提醒周期",
  stale_after_months: "证据较旧期限",
  require_manager: "需要经理确认",
  category_overrides: "各类能力的确认规则",
  owner_review_days: "本人过目期限（天）",
  deployment: "Jira 类型",
  base_url: "站点地址",
  auth_type: "认证方式",
  username: "账号",
  start_date: "同步起始日期",
  include_description: "同步任务描述",
  sync_schedule: "同步周期",
  identity_id: "工作记录账号",
  employee_id: "关联员工",
  run_id: "同步记录",
}
const values: Record<string, string> = {
  feishu: "飞书",
  cloud_api: "云端模型服务",
  local: "本地模型",
  openai_compatible: "其他兼容模型服务",
  chatgpt_subscription: "ChatGPT 订阅",
  "openai-compatible": "其他兼容模型服务",
  ollama: "本地模型",
  openai: "OpenAI",
  anthropic: "Anthropic",
  deepseek: "DeepSeek",
  qwen: "通义千问",
  moonshotai: "Kimi 国际",
  "moonshotai-cn": "Kimi 中国",
  "kimi-coding": "Kimi Coding",
  openrouter: "OpenRouter",
  quarterly: "每季度",
  yearly: "每年",
  off: "不提醒",
  monthly: "每月",
  cloud: "云端版",
  data_center: "企业自行部署版",
  api_token: "连接密钥",
  pat: "个人访问令牌",
  basic: "用户名和密码",
  true: "需要",
  false: "不需要",
}

function display(value: unknown, field: string): string {
  if (field === "enabled") return value ? "启用" : "关闭"
  if (field === "reasoning_effort")
    return (
      {
        off: "默认",
        minimal: "极少",
        low: "较少",
        medium: "适中",
        high: "较深",
        xhigh: "最深",
      }[String(value)] ?? "默认"
    )
  if (field === "allowed_clients" && Array.isArray(value))
    return value.join("、") || "允许所有客户端"
  if (field === "sync_schedule" && value === "off") return "关闭"
  if (field === "include_description") return value ? "同步" : "不同步"
  if (field === "category_overrides" && value && typeof value === "object")
    return (
      Object.entries(value)
        .map(
          ([category, required]) =>
            `${category}：${required ? "需要" : "不需要"}经理确认`,
        )
        .join("；") || "沿用总规则"
    )
  if (typeof value === "object") return JSON.stringify(value)
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
  const [, copy] = useCopyToClipboard()
  const client = useQueryClient()
  const [oneTime, setOneTime] = useState<
    NonNullable<ChangeSetApplied["one_time"]>
  >([])
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
      if (kind === "apply") {
        const { one_time, ...data } = (
          await AdminAssistantService.assistantApplyChangeSet({
            path,
            body: { revision: current.revision },
          })
        ).data
        setOneTime(one_time ?? [])
        return data
      }
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
  const test = useMutation({
    mutationFn: async (item: number) =>
      (
        await AdminAssistantService.assistantTestChangeSet({
          path,
          body: { item },
        })
      ).data,
    onSuccess: () => client.invalidateQueries(),
  })
  const busy =
    disabled ||
    action.isPending ||
    test.isPending ||
    query.isFetching ||
    query.isError
  return (
    <section
      aria-label={inline ? "页面草稿确认" : "设置改动确认卡"}
      className="space-y-4 rounded-xl border border-primary/25 bg-background p-4 shadow-sm"
    >
      <div className="flex items-center justify-between gap-3 border-b pb-3">
        <h3 className="font-semibold">
          {current.undo_of_id ? "撤销前请确认" : "请核对改动"}
        </h3>
        <span role="status" className="text-xs text-muted-foreground">
          {confirmAgain ? "请再次确认" : statuses[current.status]}
        </span>
      </div>
      {current.items.map((item, i) => (
        <div key={i} className="space-y-2">
          <p className="text-sm font-medium">{item.title}</p>
          {item.function === "apply_roster_import" && (
            <p className="text-sm">
              请核对上方名单预览。确认后更新员工信息，离职人员的账号会停用。
            </p>
          )}
          {item.undoable &&
            Object.keys(item.after)
              .filter(
                (field) =>
                  JSON.stringify(item.before?.[field]) !==
                  JSON.stringify(item.after[field]),
              )
              .map((field) => (
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
                            title === "修改后"
                              ? "font-semibold text-primary"
                              : ""
                          }
                        >
                          {value == null
                            ? "未设置"
                            : `${display(value, field)}${field === "stale_after_months" ? " 个月" : ""}`}
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
          {current.status === "draft" && item.testable && (
            <div className="space-y-2">
              <Button
                variant="outline"
                disabled={busy}
                onClick={() => test.mutate(i)}
              >
                {test.isPending && test.variables === i
                  ? "正在测试…"
                  : "测试连接"}
              </Button>
              {test.data && test.variables === i && (
                <p role={test.data.ok ? "status" : "alert"} className="text-sm">
                  {test.data.ok
                    ? "测试连接：成功"
                    : `测试连接：失败，${test.data.message}`}
                </p>
              )}
            </div>
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
      {oneTime.map((output, i) => (
        <div key={i} className="space-y-2 rounded-lg border p-3 text-sm">
          <p>初始密码和账号表只显示这一次，请妥善保管。</p>
          {output.type === "initial_password" ? (
            <>
              <p>
                {output.display_name} · {output.username}
              </p>
              <label className="block">
                初始密码
                <input
                  aria-label="初始密码"
                  readOnly
                  value={output.initial_password}
                  className="ml-2 rounded border p-2"
                />
              </label>
              <Button
                variant="outline"
                onClick={() => copy(output.initial_password)}
              >
                复制初始密码
              </Button>
            </>
          ) : (
            <Button
              variant="outline"
              onClick={() => {
                const bytes = Uint8Array.from(
                  atob(output.content_base64),
                  (c) => c.charCodeAt(0),
                )
                const url = URL.createObjectURL(
                  new Blob([bytes], {
                    type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                  }),
                )
                const link = document.createElement("a")
                link.href = url
                link.download = output.filename
                link.click()
                URL.revokeObjectURL(url)
              }}
            >
              下载账号表
            </Button>
          )}
        </div>
      ))}
      <SetupError error={action.error ?? test.error ?? query.error} />
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
        current.items.every(
          (item) => item.before !== null && item.undoable,
        ) && (
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
