import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link, useNavigate } from "@tanstack/react-router"
import { AxiosError } from "axios"
import { useEffect, useRef, useState } from "react"
import {
  AdminAssistantService,
  type AdminSessionMessage,
  type AdminTurnInput,
  type AdminTurnResponse,
} from "@/client"
import {
  actorLabels,
  auditLabels,
  targetLabels,
} from "@/components/Admin/auditLabels"
import { TodayPanel } from "@/components/Admin/TodayPanel"
import { BotMessage } from "@/components/Bot/BotMessage"
import { RosterPreview } from "@/components/Roster/RosterPreview"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import { useAdminAvailability } from "@/hooks/useAdminAvailability"
import { rememberConversation } from "@/hooks/useCurrentConversation"
import { ChangeSetCard } from "./ChangeSetCard"
import { FileSummaryPreview } from "./FileSummaryPreview"
import { FileUploadCard } from "./FileUploadCard"
import { SecretInputCard } from "./SecretInputCard"

const labels: Record<string, string> = {
  id: "编号",
  employee_code: "工号",
  display_name: "姓名",
  title: "职位",
  work_email: "工作邮箱",
  employment_status: "在职状态",
  department: "部门",
  account: "账号",
  roles: "角色",
  username: "登录邮箱",
  role: "角色",
  department_id: "部门编号",
  user_id: "账号编号",
  kind: "来源",
  status: "状态",
  trigger: "启动方式",
  attempts: "尝试次数",
  counts: "处理数量",
  error: "问题说明",
  filename: "文件名",
  created_at: "创建时间",
  started_at: "开始时间",
  finished_at: "完成时间",
  updated_at: "更新时间",
  platform: "消息平台",
  enabled: "已开启",
  email_auto_bind: "自动匹配邮箱",
  has_credentials: "已填写连接信息",
  summary: "摘要",
  action: "操作",
  actor_id: "操作人",
  actor_type: "操作来源",
  target_id: "对象编号",
  target_type: "对象类型",
  details: "详情",
  open: "待处理",
  upheld: "维持原决定",
  reopened: "允许重新提交",
  preview: "待确认",
  applied: "已导入",
  active: "正常",
  inactive: "已离职",
  none: "未生成",
  pending_first_login: "待首次登录",
  disabled: "已停用",
  org_admin: "企业管理员",
  dept_manager: "部门经理",
  employee: "员工",
  queued: "排队中",
  running: "进行中",
  succeeded: "已完成",
  failed: "失败",
  manual: "手动",
  retry: "重试",
  schedule: "定期",
  feishu: "飞书",
  jira: "工作记录",
  csv: "文件导入",
}

function cellText(value: unknown, field = ""): string {
  if (value == null) return "—"
  if (typeof value === "boolean") return value ? "是" : "否"
  if (Array.isArray(value))
    return value.map((entry) => cellText(entry, field)).join("、")
  if (typeof value === "object") {
    for (const key of field === "roles"
      ? ["role"]
      : ["display_name", "name", "status"]) {
      const entry = Object.entries(value).find(([field]) => field === key)?.[1]
      if (entry != null) return cellText(entry, key)
    }
    return Object.entries(value)
      .map(([key, entry]) => cellText(entry, key))
      .join("、")
  }
  return [
    "status",
    "employment_status",
    "role",
    "platform",
    "kind",
    "trigger",
    "action",
    "actor_type",
    "target_type",
  ].includes(field)
    ? (labels[String(value)] ??
        auditLabels[String(value)] ??
        actorLabels[String(value)] ??
        targetLabels[String(value)] ??
        String(value))
    : String(value)
}

export function AdminAssistantPanel({
  orgId,
  storageKey,
  sessionId,
  navigationKey,
  prefill,
}: {
  orgId: string
  storageKey: string
  sessionId?: string
  navigationKey?: string
  prefill?: { text: string }
}) {
  const client = useQueryClient()
  const navigate = useNavigate()
  const availability = useAdminAvailability()
  const available = availability.data?.available === true
  const [uploading, setUploading] = useState(false)
  const [text, setText] = useState(prefill?.text ?? "")
  const bottom = useRef<HTMLDivElement>(null)
  const preserveText = useRef(false)
  const selection = useRef(navigationKey)
  selection.current = navigationKey
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])
  const query = { org_id: orgId }
  const messageKey = ["admin-assistant-messages", orgId, sessionId]
  const history = useQuery({
    queryKey: messageKey,
    enabled: !!sessionId,
    retry: false,
    queryFn: async () =>
      (
        await AdminAssistantService.assistantListMessages({
          query,
          path: { session_id: sessionId! },
        })
      ).data,
  })
  const messages = history.data ?? []
  const expired =
    history.error instanceof AxiosError &&
    history.error.response?.status === 404
  useEffect(() => {
    const follow = (event: Event) => {
      if (!(event instanceof StorageEvent)) {
        if (!localStorage.getItem(storageKey)) setText("")
        return
      }
      if (event.key !== storageKey) return
      preserveText.current = true
      navigate({
        to: "/admin/assistant",
        search: { session: event.newValue || undefined },
      })
    }
    window.addEventListener("storage", follow)
    return () => window.removeEventListener("storage", follow)
  }, [storageKey, navigate])
  useEffect(() => {
    if (
      history.error instanceof AxiosError &&
      history.error.response?.status === 404
    ) {
      rememberConversation(storageKey, "")
      client.invalidateQueries({
        queryKey: ["conversation-history", orgId, true],
      })
    }
  }, [history.error, storageKey, orgId, client])
  const send = useMutation({
    mutationFn: async (body: AdminTurnInput) => {
      const id =
        (!expired ? sessionId : undefined) ??
        (await AdminAssistantService.assistantCreateSession({ query })).data
          .session_id
      const response = await AdminAssistantService.assistantTurn({
        query,
        path: { session_id: id },
        body,
        timeout: 75000,
      })
      return { id, data: response.data, navigationKey }
    },
    onSuccess: ({ id, data, navigationKey: startedAt }, body) => {
      const next: Pick<AdminSessionMessage, "role" | "payload">[] = [
        ...messages,
        ...(data.messages.some(
          (message) =>
            message.type === "text" && message.text === "请用安全输入框填写",
        )
          ? []
          : [{ role: "user" as const, payload: body }]),
        { role: "assistant", payload: data },
      ]
      client.setQueryData(["admin-assistant-messages", orgId, id], next)
      if (!mounted.current || selection.current !== startedAt) {
        client.invalidateQueries({
          predicate: (query) =>
            query.queryKey[0] !== "admin-assistant-messages",
        })
        return
      }
      rememberConversation(storageKey, id)
      navigate({
        to: "/admin/assistant",
        search: { session: id },
        replace: true,
      })
      client.invalidateQueries({
        predicate: (query) => query.queryKey[0] !== "admin-assistant-messages",
      })
    },
  })
  const busy =
    (!!sessionId && history.isFetching) || send.isPending || uploading
  const uploaded = (response: AdminTurnResponse) => {
    client.setQueryData(messageKey, [
      ...messages,
      { role: "assistant", payload: response },
    ])
    client.invalidateQueries()
  }
  useEffect(() => {
    if (sessionId) rememberConversation(storageKey, sessionId)
    if (!preserveText.current) setText(prefill?.text ?? "")
    preserveText.current = false
    send.reset()
  }, [sessionId, storageKey, prefill, send.reset])
  useEffect(() => {
    if (messages.length || busy)
      bottom.current?.scrollIntoView({ block: "end" })
  }, [messages.length, busy])
  const content = (
    <>
      <div
        className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4 text-sm"
        role="log"
        aria-label="管理助手对话"
        aria-busy={busy}
      >
        {!available && (
          <p role="status" className="rounded-lg border bg-muted/40 p-4">
            {availability.data?.reason || "正在检查管理助手…"}
          </p>
        )}
        <SetupError error={availability.error} />
        {!sessionId && (
          <TodayPanel orgId={orgId} onPrompt={(text) => setText(text)} />
        )}
        <p className="rounded-xl border-l-4 border-primary bg-muted/40 p-4">
          试着问「今天有什么要处理的」。我会结合首页状态，帮你找到对应页面。
        </p>
        {messages.map((entry, index) => (
          <div
            key={`${sessionId}:${index}`}
            className={`space-y-3 ${entry.role === "user" ? "ml-8 rounded-xl bg-primary/10 p-3 whitespace-pre-wrap" : "mr-1"}`}
          >
            <p className="text-xs text-muted-foreground">
              {entry.role === "user" ? "我" : "管理助手"}
            </p>
            {"messages" in entry.payload
              ? entry.payload.messages.map((message, i) =>
                  message.type === "link" ? (
                    <Link
                      key={i}
                      to={message.page}
                      className="block rounded-xl border p-4 underline"
                    >
                      {message.title}
                    </Link>
                  ) : message.type === "file_upload" ? (
                    <FileUploadCard
                      key={i}
                      orgId={orgId}
                      sessionId={sessionId!}
                      card={message}
                      disabled={busy}
                      onUploaded={uploaded}
                      onUploading={setUploading}
                    />
                  ) : message.type === "file_preview" ? (
                    <div
                      key={i}
                      className="rounded-xl border bg-background p-4"
                    >
                      {"counts" in message.preview ? (
                        <FileSummaryPreview preview={message.preview} />
                      ) : (
                        <RosterPreview preview={message.preview} />
                      )}
                    </div>
                  ) : message.type === "admin_change_set" ? (
                    <ChangeSetCard
                      key={i}
                      orgId={orgId}
                      changeSet={message.change_set}
                      disabled={busy}
                    />
                  ) : message.type === "secret_input" ? (
                    <SecretInputCard
                      key={i}
                      orgId={orgId}
                      card={message}
                      disabled={busy}
                    />
                  ) : message.type === "table" ? (
                    <div
                      key={i}
                      className="overflow-x-auto rounded-xl border p-3"
                    >
                      <p>
                        {message.title}
                        {message.queried_at && (
                          <span className="ml-2 text-xs text-muted-foreground">
                            查询于{" "}
                            {new Date(message.queried_at).toLocaleTimeString(
                              "zh-CN",
                              { hour: "2-digit", minute: "2-digit" },
                            )}
                          </span>
                        )}
                        （共 {message.total} 条，最多显示 20 条）
                      </p>
                      <table className="w-full text-left text-sm">
                        <thead>
                          <tr>
                            {message.columns.map((column) => (
                              <th key={column} className="p-2">
                                {labels[column] ?? "信息"}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {message.rows.map((row, rowIndex) => (
                            <tr key={rowIndex}>
                              {row.map((cell, cellIndex) => (
                                <td
                                  key={cellIndex}
                                  className="max-w-64 break-words p-2"
                                >
                                  {cellText(cell, message.columns[cellIndex])}
                                </td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      <Link to={message.page} className="underline">
                        打开对应页面
                      </Link>
                    </div>
                  ) : (
                    <BotMessage
                      key={i}
                      message={message}
                      disabled={busy}
                      onAction={() => {}}
                    />
                  ),
                )
              : entry.payload.text}
          </div>
        ))}
        <div ref={bottom} />
        {busy && <p role="status">正在处理，请稍候…</p>}
        <SetupError error={(!expired ? history.error : null) ?? send.error} />
        {expired && <p role="status">这段对话已过期被清除，已为你新开对话</p>}
        {history.isError && (
          <Button onClick={() => history.refetch()}>重试加载对话</Button>
        )}
      </div>
      <div className="space-y-2 border-t px-4 pt-3 text-xs">
        <h2 className="font-semibold">我能帮你做什么</h2>
        <div className="flex flex-wrap gap-2">
          {[
            "查看同步与起草进度",
            "修改提醒周期",
            "查看员工名单和账号",
            "查看离职人员",
            "检查飞书连接",
            "排查同步失败",
          ].map((example) => (
            <button
              type="button"
              key={example}
              className="rounded-full border px-3 py-1"
              onClick={() => setText(example)}
            >
              {example}
            </button>
          ))}
        </div>
        <p className="text-muted-foreground">
          可以查看信息、起草提醒设置改动，点确认卡后才会生效。备份恢复、升级、重置请联系部署人员；不做绩效和薪酬判断。
        </p>
      </div>
      <form
        className="space-y-3 border-t p-4"
        onSubmit={(event) => {
          event.preventDefault()
          if (!text.trim() || busy || !available) return
          const body = { text: text.trim(), page: "/admin" as const }
          setText("")
          send.mutate(body)
        }}
      >
        <label htmlFor="admin-assistant-input" className="sr-only">
          发给管理助手的消息
        </label>
        <textarea
          id="admin-assistant-input"
          className="min-h-20 w-full rounded-lg border bg-background p-3 text-sm"
          placeholder="今天有什么要处理的？"
          maxLength={10000}
          value={text}
          onChange={(event) => setText(event.target.value)}
          disabled={busy || !available}
        />
        <div className="flex justify-between gap-3">
          <Button disabled={busy || !available || !text.trim()}>发送</Button>
        </div>
      </form>
    </>
  )
  return <div className="flex h-full min-h-0 flex-col">{content}</div>
}
