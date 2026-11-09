import { useMutation } from "@tanstack/react-query"
import { useLocation } from "@tanstack/react-router"
import { AxiosError } from "axios"
import { useEffect, useRef, useState } from "react"
import {
  AdminAssistantService,
  type AdminSessionMessage,
  type AdminTurnInput,
} from "@/client"
import {
  actorLabels,
  auditLabels,
  targetLabels,
} from "@/components/Admin/auditLabels"
import { BotMessage } from "@/components/Bot/BotMessage"
import { BotPanel } from "@/components/Bot/BotPanel"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet"
import useAuth from "@/hooks/useAuth"
import { useOrg } from "@/hooks/useOrg"
import { ChangeSetCard } from "./ChangeSetCard"

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

const pages: AdminTurnInput["page"][] = [
  "/admin",
  "/admin/people",
  "/admin/people/$employeeId",
  "/admin/people?tab=accounts",
  "/admin/people?tab=roster",
  "/admin/people?tab=roles",
  "/admin/people?tab=identities",
  "/admin/people?tab=appeals",
  "/admin/rules",
  "/admin/rules?tab=dictionary",
  "/admin/rules?tab=approval",
  "/admin/rules?tab=drafting",
  "/admin/rules?tab=reminders",
  "/admin/rules?tab=profile-import",
  "/admin/data-ai",
  "/admin/data-ai?tab=sources",
  "/admin/data-ai?tab=model",
  "/admin/data-ai?tab=bot",
  "/admin/connections",
  "/admin/connections?tab=im",
  "/admin/connections?tab=mcp",
  "/admin/audit",
]

export function AssistantEntry() {
  const { org, roles } = useOrg()
  const { user } = useAuth()
  const location = useLocation()
  if (
    !org ||
    !user ||
    !roles.includes("org_admin") ||
    !/^\/admin(?:\/|$)/.test(location.pathname)
  )
    return <BotPanel />
  const path = location.pathname.replace(/\/$/, "")
  const tab = new URLSearchParams(location.searchStr).get("tab")
  const current = path.startsWith("/admin/people/")
    ? "/admin/people/$employeeId"
    : path + (tab ? `?tab=${tab}` : "")
  const page =
    pages.find((value) => value === current) ??
    pages.find((value) => value === path)
  return page ? (
    <AdminAssistantPanel
      key={`${user.id}:${org.id}`}
      orgId={org.id}
      storageKey={`admin-assistant:${user.id}:${org.id}`}
      page={page}
    />
  ) : null
}

export function AdminAssistantPanel({
  orgId,
  storageKey,
  page,
  fullScreen = false,
  prefill,
}: {
  orgId: string
  storageKey: string
  page: AdminTurnInput["page"]
  fullScreen?: boolean
  prefill?: { text: string }
}) {
  const [open, setOpen] = useState(false)
  const [sessionId, setSessionId] = useState("")
  const [messages, setMessages] = useState<
    Pick<AdminSessionMessage, "role" | "payload">[]
  >([])
  const [text, setText] = useState(prefill?.text ?? "")
  const bottom = useRef<HTMLDivElement>(null)
  const query = { org_id: orgId }
  const initialize = useMutation({
    mutationFn: async (fresh: boolean) => {
      let id = fresh ? null : sessionStorage.getItem(storageKey)
      if (id) {
        try {
          return {
            id,
            messages: (
              await AdminAssistantService.assistantListMessages({
                query,
                path: { session_id: id },
              })
            ).data,
          }
        } catch (error) {
          if (!(error instanceof AxiosError) || error.response?.status !== 404)
            throw error
        }
      }
      id = (await AdminAssistantService.assistantCreateSession({ query })).data
        .session_id
      sessionStorage.setItem(storageKey, id)
      return { id, messages: [] }
    },
    onSuccess: (data) => {
      setSessionId(data.id)
      setMessages(data.messages)
    },
  })
  const send = useMutation({
    mutationFn: (body: AdminTurnInput) =>
      AdminAssistantService.assistantTurn({
        query,
        path: { session_id: sessionId },
        body,
        timeout: 75000,
      }),
    onSuccess: ({ data }) =>
      setMessages((previous) => [
        ...previous,
        { role: "assistant", payload: data },
      ]),
  })
  const busy = initialize.isPending || send.isPending
  useEffect(() => {
    if (
      fullScreen &&
      !sessionId &&
      !initialize.isPending &&
      !initialize.isError
    )
      initialize.mutate(false)
  }, [
    fullScreen,
    sessionId,
    initialize.isPending,
    initialize.isError,
    initialize.mutate,
  ])
  useEffect(() => {
    if (prefill) setText(prefill.text)
  }, [prefill])
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
        <p className="rounded-xl border-l-4 border-primary bg-muted/40 p-4">
          试着问「今天有什么要处理的」。我会结合首页状态，帮你找到对应页面。
        </p>
        {messages.map((entry, index) => (
          <div
            key={index}
            className={`space-y-3 ${entry.role === "user" ? "ml-8 rounded-xl bg-primary/10 p-3 whitespace-pre-wrap" : "mr-1"}`}
          >
            <p className="text-xs text-muted-foreground">
              {entry.role === "user" ? "我" : "管理助手"}
            </p>
            {"messages" in entry.payload
              ? entry.payload.messages.map((message, i) =>
                  message.type === "link" ? (
                    <a
                      key={i}
                      href={message.page}
                      className="block rounded-xl border p-4 underline"
                    >
                      {message.title}
                    </a>
                  ) : message.type === "admin_change_set" ? (
                    <ChangeSetCard
                      key={i}
                      orgId={orgId}
                      changeSet={message.change_set}
                      disabled={busy}
                    />
                  ) : message.type === "table" ? (
                    <div
                      key={i}
                      className="overflow-x-auto rounded-xl border p-3"
                    >
                      <p>
                        {message.title}（共 {message.total} 条，最多显示 20 条）
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
                      <a href={message.page} className="underline">
                        打开对应页面
                      </a>
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
        <SetupError error={initialize.error ?? send.error} />
        {initialize.isError && (
          <Button onClick={() => initialize.mutate(false)}>重试加载对话</Button>
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
          if (!text.trim() || busy || !sessionId) return
          const body = { text: text.trim(), page }
          setMessages((previous) => [
            ...previous,
            { role: "user", payload: body },
          ])
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
          disabled={busy || !sessionId}
        />
        <div className="flex justify-between gap-3">
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={() => initialize.mutate(true)}
          >
            新对话
          </Button>
          <Button disabled={busy || !sessionId || !text.trim()}>发送</Button>
        </div>
      </form>
    </>
  )
  if (fullScreen)
    return <div className="flex h-full min-h-0 flex-col">{content}</div>
  return (
    <Sheet
      open={open}
      onOpenChange={(value) => {
        setOpen(value)
        if (value && !sessionId && !busy) initialize.mutate(false)
      }}
    >
      <SheetTrigger asChild>
        <Button
          className="fixed bottom-5 right-5 z-20 rounded-full shadow-lg"
          aria-label="打开管理助手"
        >
          管理助手
        </Button>
      </SheetTrigger>
      <SheetContent className="inset-0 h-dvh w-full gap-0 sm:inset-auto sm:bottom-5 sm:right-5 sm:h-[min(740px,calc(100dvh-40px))] sm:w-[440px] sm:max-w-[440px] sm:rounded-2xl sm:border">
        <SheetHeader className="border-b pr-12">
          <SheetTitle>管理助手</SheetTitle>
          <SheetDescription>
            查看今天要处理的事，也能起草提醒设置改动；点确认卡后才会生效。聊天默认在闲置两小时后清除；开启保留时按企业设置清理。
          </SheetDescription>
          <a href="/admin/assistant" className="text-sm underline">
            全屏打开
          </a>
        </SheetHeader>
        {content}
      </SheetContent>
    </Sheet>
  )
}
