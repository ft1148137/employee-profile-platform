import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link, useNavigate } from "@tanstack/react-router"
import { AxiosError } from "axios"
import { useEffect, useRef, useState } from "react"
import {
  AdminAssistantService,
  type AdminSessionMessage,
  type AdminTurnInput,
  type AdminTurnResponse,
  type AdminUserMessage,
} from "@/client"
import {
  actorLabels,
  auditLabels,
  targetLabels,
} from "@/components/Admin/auditLabels"
import { TodayPanel } from "@/components/Admin/TodayPanel"
import { BotMessage } from "@/components/Bot/BotMessage"
import { ChatComposer } from "@/components/Chat/ChatComposer"
import { ChatMessages } from "@/components/Chat/ChatMessages"
import { useStreamingTurn } from "@/components/Chat/useStreamingTurn"
import { RosterPreview } from "@/components/Roster/RosterPreview"
import { SetupError } from "@/components/Setup/SetupError"
import { SetupStartPanel } from "@/components/Setup/SetupStartPanel"
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

function displayInput(body: AdminUserMessage): AdminUserMessage {
  return /(?<![A-Za-z0-9])sk-\S+|(?<![A-Za-z0-9])[A-Za-z0-9]{32,}(?![A-Za-z0-9])/.test(
    body.text,
  )
    ? { ...body, text: "已隐藏可能的密钥，请用安全输入框填写" }
    : body
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
  const [composerVersion, setComposerVersion] = useState(0)
  const [pendingMessage, setPendingMessage] = useState<AdminUserMessage | null>(
    null,
  )
  const turnSession = useRef(sessionId)
  const creatingSession = useRef<{
    navigationKey?: string
    promise: Promise<string>
  } | null>(null)
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
        if (!localStorage.getItem(storageKey)) {
          setText("")
          creatingSession.current = null
          setComposerVersion((version) => version + 1)
        }
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
  const ensureSession = async (signal?: AbortSignal) => {
    if (!expired && sessionId) return sessionId
    if (
      !creatingSession.current ||
      creatingSession.current.navigationKey !== navigationKey
    ) {
      const promise = AdminAssistantService.assistantCreateSession({
        query,
        signal,
      })
        .then(({ data }) => data.session_id)
        .catch((error) => {
          if (creatingSession.current?.promise === promise)
            creatingSession.current = null
          throw error
        })
      creatingSession.current = { navigationKey, promise }
    }
    return creatingSession.current.promise
  }
  const stream = useStreamingTurn<AdminTurnInput, AdminTurnResponse>(
    `/api/v1/admin-assistant/sessions/${sessionId ?? ""}/turns?org_id=${orgId}`,
    async (signal) => {
      const id = await ensureSession(signal)
      if (!signal.aborted) turnSession.current = id
      return `/api/v1/admin-assistant/sessions/${id}/turns?org_id=${orgId}`
    },
  )
  const send = useMutation({
    onMutate: () => navigationKey,
    mutationFn: async (body: AdminUserMessage) => {
      const result = await stream.mutateAsync({
        text: body.text,
        page: body.page,
        attachment_ids: body.attachment_ids,
      })
      return { id: turnSession.current!, result, navigationKey }
    },
    onSuccess: ({ id, result, navigationKey: startedAt }, body) => {
      if (!result) return
      const { response: data } = result
      client.setQueryData<
        (Pick<AdminSessionMessage, "role" | "payload"> & {
          created_at?: string
        })[]
      >(["admin-assistant-messages", orgId, id], (previous = []) => [
        ...previous,
        ...(data.messages.some(
          (message) =>
            message.type === "text" && message.text === "请用安全输入框填写",
        )
          ? []
          : [
              {
                role: "user" as const,
                payload: displayInput(body),
                created_at: result.startedAt,
              },
            ]),
        {
          role: "assistant",
          payload: data,
          created_at: result.created_at,
        },
      ])
      if (mounted.current && selection.current === startedAt) {
        rememberConversation(storageKey, id)
        navigate({
          to: "/admin/assistant",
          search: { session: id },
          replace: true,
        })
      }
      client.invalidateQueries({
        predicate: (query) =>
          !["admin-assistant-messages", "chat-attachment"].includes(
            String(query.queryKey[0]),
          ),
      })
    },
    onSettled: (_data, _error, _body, startedAt) => {
      if (selection.current === startedAt) setPendingMessage(null)
    },
  })
  const busy =
    (!!sessionId && history.isFetching) ||
    (history.isError && !expired) ||
    send.isPending ||
    uploading
  const uploaded = (response: AdminTurnResponse) => {
    client.setQueryData(messageKey, [
      ...messages,
      { role: "assistant", payload: response },
    ])
    client.invalidateQueries()
  }
  useEffect(() => {
    if (sessionId || !preserveText.current)
      rememberConversation(storageKey, sessionId ?? "")
    if (!preserveText.current) setText(prefill?.text ?? "")
    preserveText.current = false
    creatingSession.current = null
    turnSession.current = sessionId
    send.reset()
    setPendingMessage(null)
  }, [sessionId, storageKey, prefill, send.reset])
  useEffect(() => {
    if (messages.length || busy || stream.text || stream.tools.length)
      bottom.current?.scrollIntoView({ block: "end" })
  }, [messages.length, busy, stream.text, stream.tools])
  useEffect(() => {
    if (navigationKey) return stream.stop
  }, [navigationKey, stream.stop])
  const content = (
    <>
      <div
        className="min-h-0 flex-1 overflow-y-auto px-4 py-6 text-sm [&>*]:mx-auto [&>*]:max-w-3xl [&>*+*]:mt-4"
        role="log"
        aria-label="管理助手对话"
        aria-busy={busy}
      >
        {!available && (
          <p role="status" className="rounded-lg border bg-muted/40 p-4">
            {availability.data?.reason || "正在检查管理助手…"}
            {availability.data?.reason?.includes("模型") && (
              <span className="mt-2 block">
                模型未配置或不可用。
                <Link
                  to="/admin/data-ai"
                  search={{ tab: "model" }}
                  className="underline"
                >
                  去配置模型
                </Link>
              </span>
            )}
          </p>
        )}
        <SetupError error={availability.error} />
        {!sessionId && !messages.length && !pendingMessage && (
          <SetupStartPanel
            orgId={orgId}
            available={available}
            onStart={() => {
              const body = { text: "帮我完成首次设置", page: "/admin" as const }
              setPendingMessage(body)
              send.mutate(body)
            }}
          >
            <div className="space-y-8 py-6">
              <div className="space-y-3 text-center">
                <h2 className="text-2xl font-semibold">
                  你好，今天有什么要处理的？
                </h2>
                <p className="text-muted-foreground">
                  试着问「今天有什么要处理的」。我会结合首页状态，帮你找到对应页面。
                </p>
              </div>
              <TodayPanel orgId={orgId} onPrompt={setText} />
              <section className="space-y-3" aria-label="可以这样问">
                <h2 className="font-semibold">可以这样问</h2>
                <div className="flex flex-wrap gap-2">
                  {[
                    "查看同步与起草进度",
                    "修改提醒周期",
                    "查看员工名单和账号",
                    "查看离职人员",
                    "检查飞书连接",
                    "排查同步失败",
                  ].map((example) => (
                    <Button
                      type="button"
                      variant="outline"
                      key={example}
                      className="h-auto rounded-full px-3 py-2 text-xs font-normal"
                      onClick={() => setText(example)}
                    >
                      {example}
                    </Button>
                  ))}
                </div>
                <p className="text-xs text-muted-foreground">
                  可以查看信息、起草提醒设置改动，点确认卡后才会生效。备份恢复、升级、重置请联系部署人员；不做绩效和薪酬判断。
                </p>
              </section>
            </div>
          </SetupStartPanel>
        )}
        <ChatMessages
          onDownload={async (id) =>
            (
              await AdminAssistantService.assistantDownloadAttachment({
                query,
                path: {
                  session_id:
                    (!expired ? sessionId : undefined) ?? turnSession.current!,
                  attachment_id: id,
                },
              })
            ).data
          }
          messages={
            pendingMessage
              ? [
                  ...messages,
                  { role: "user" as const, payload: pendingMessage },
                ]
              : messages
          }
          text={stream.text}
          tools={stream.tools}
          stopped={stream.stopped}
          renderMessage={(entry, index) => (
            <div key={`${sessionId}:${index}`} className="space-y-4">
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
                        <table className="w-max min-w-full text-left text-sm">
                          <thead>
                            <tr>
                              {message.columns.map((column) => (
                                <th
                                  key={column}
                                  className="whitespace-nowrap p-2"
                                >
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
                                    className="min-w-[8em] max-w-64 break-words p-2"
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
          )}
        />
        <div ref={bottom} />
        {busy && <p role="status">正在处理，请稍候…</p>}
        <SetupError error={(!expired ? history.error : null) ?? send.error} />
        {expired && <p role="status">这段对话已过期被清除，已为你新开对话</p>}
        {history.isError && (
          <Button onClick={() => history.refetch()}>重试加载对话</Button>
        )}
      </div>
      <ChatComposer
        key={`${navigationKey}:${composerVersion}`}
        id="admin-assistant-input"
        label="发给管理助手的消息"
        placeholder="给管理助手发消息…"
        text={text}
        onChange={setText}
        onUpload={async (file) => {
          const id = await ensureSession()
          if (selection.current === navigationKey) turnSession.current = id
          return (
            await AdminAssistantService.assistantUploadAttachment({
              query,
              path: { session_id: id },
              body: { file },
            })
          ).data
        }}
        onSend={async (attachments) => {
          const body = {
            text: text.trim() || "请查看附件",
            page: "/admin" as const,
            attachments,
            attachment_ids: attachments.map((item) => item.id),
          }
          setText("")
          setPendingMessage(displayInput(body))
          const { result } = await send.mutateAsync(body)
          return !!result
        }}
        busy={stream.isPending}
        disabled={busy || !available}
        onStop={stream.stop}
      />
    </>
  )
  return <div className="flex h-full min-h-0 flex-col">{content}</div>
}
