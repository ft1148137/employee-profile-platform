import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link, useNavigate } from "@tanstack/react-router"
import { AxiosError } from "axios"
import { LoaderCircle } from "lucide-react"
import { useEffect, useRef, useState } from "react"
import {
  type AttachmentPublic,
  BotService,
  EmployeeRequestsService,
  type SessionMessage,
  type TurnInput,
  type TurnResponse,
} from "@/client"
import { ChatComposer, readChatDraft } from "@/components/Chat/ChatComposer"
import { ChatMessages } from "@/components/Chat/ChatMessages"
import { useStreamingTurn } from "@/components/Chat/useStreamingTurn"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import type { AssistantMode } from "@/hooks/useAssistantWorkspace"
import { rememberConversation } from "@/hooks/useCurrentConversation"
import { useOrg } from "@/hooks/useOrg"
import { type BotAction, BotMessage } from "./BotMessage"
import { requestCardHistory } from "./requestCards"

export function BotConversation({
  orgId,
  employeeId,
  storageKey,
  selectedSession,
  restart,
  mode,
  prompt,
}: {
  orgId: string
  employeeId: string
  storageKey: string
  selectedSession?: string
  restart?: number
  mode?: AssistantMode
  prompt?: string
}) {
  const { managedDepartmentIds } = useOrg()
  const description = `${managedDepartmentIds.length ? "可以操作你本人和你管辖部门员工的画像" : "只操作你本人的画像"}。画像不用于绩效、薪酬和晋升。聊天原文默认不保存；未开启保留时，会话闲置 2 小时后清除。`
  const client = useQueryClient()
  const navigate = useNavigate()
  const [sessionId, setSessionId] = useState("")
  const [messages, setMessages] = useState<
    (Pick<SessionMessage, "role" | "payload"> &
      Partial<Pick<SessionMessage, "created_at">>)[]
  >([])
  const [text, setText] = useState(prompt ?? "")
  const [expired, setExpired] = useState(false)
  const replacementSession = useRef("")
  const [conflict, setConflict] = useState<"preview" | "withdraw" | null>(null)
  const bottom = useRef<HTMLDivElement>(null)
  const navigation = `${selectedSession ?? ""}:${restart ?? ""}`
  const selection = useRef(navigation)
  selection.current = navigation
  const initialization = useRef(0)
  const preserveText = useRef(false)
  const query = { org_id: orgId }
  const dictionary = useQuery({
    queryKey: ["my-capabilities", orgId],
    queryFn: async () =>
      (await EmployeeRequestsService.readMyCapabilities({ path: query })).data,
  })
  const initialize = useMutation({
    onMutate: () => ++initialization.current,
    mutationFn: async (selection?: string) => {
      let expired = false
      let id = selection ?? localStorage.getItem(storageKey)
      if (id) {
        try {
          const messages = (
            await BotService.listMessages({ query, path: { session_id: id } })
          ).data
          return { id, messages, expired }
        } catch (error) {
          if (!(error instanceof AxiosError) || error.response?.status !== 404)
            throw error
          expired = true
        }
      }
      id = (await BotService.createSession({ query })).data.session_id
      return { id, messages: [], expired }
    },
    onSuccess: (data, _requested, startedAt) => {
      if (initialization.current !== startedAt) return
      preserveText.current = false
      rememberConversation(storageKey, data.id)
      if (data.expired) replacementSession.current = data.id
      setExpired(data.expired || replacementSession.current === data.id)
      if (data.expired)
        navigate({
          to: "/assistant",
          search: (previous) => ({ ...previous, session: data.id }),
          replace: true,
        })
      if (data.expired)
        client.invalidateQueries({
          queryKey: ["conversation-history", orgId, false],
        })
      setConflict(null)
      send.reset()
      setSessionId(data.id)
      setText((current) => current || readChatDraft(`${storageKey}:${data.id}`))
      setMessages(data.messages)
    },
  })
  const stream = useStreamingTurn<TurnInput, TurnResponse>(
    `/api/v1/bot/sessions/${sessionId}/turns?org_id=${orgId}`,
  )
  const send = useMutation({
    onMutate: () => navigation,
    mutationFn: async (input: TurnInput | BotAction) => {
      const options = { query, path: { session_id: sessionId } }
      return "text" in input
        ? stream.mutateAsync(input)
        : {
            response: (await BotService.act({ ...options, body: input })).data,
            created_at: new Date().toISOString(),
          }
    },
    onSuccess: (result, input, startedAt) => {
      if (selection.current !== startedAt) return
      if (!result) {
        setMessages((previous) => previous.slice(0, -1))
        return
      }
      setMessages((previous) => [
        ...previous,
        {
          role: "assistant",
          payload: result.response,
          created_at: result.created_at,
        },
      ])
      setConflict(null)
      client.invalidateQueries({ queryKey: ["conversation-history", orgId] })
      client.invalidateQueries({ queryKey: ["change-requests", orgId] })
      client.invalidateQueries({ queryKey: ["inbox", orgId] })
      client.invalidateQueries({ queryKey: ["profile", orgId] })
      if ("action" in input && input.action === "confirm_project_draft")
        client.invalidateQueries({ queryKey: ["capacity", orgId] })
    },
    onError: async (error, input, startedAt) => {
      if (selection.current !== startedAt) return
      if (!(error instanceof AxiosError) || error.response?.status !== 409) {
        setConflict(null)
        return
      }
      const withdrawing = "action" in input && input.action === "withdraw"
      setConflict(withdrawing ? "withdraw" : "preview")
      if (
        "request_id" in input &&
        "action" in input &&
        (input.action === "confirm_submit" ||
          input.action === "continue_editing" ||
          withdrawing)
      ) {
        const refreshed = await BotService.listMessages({
          query,
          path: { session_id: sessionId },
        }).catch(() => null)
        if (refreshed && selection.current === startedAt)
          setMessages(refreshed.data)
      }
    },
  })
  useEffect(() => {
    const follow = (event: StorageEvent) => {
      if (event.key === storageKey && event.newValue) {
        preserveText.current = true
        navigate({
          to: "/assistant",
          search: { mode, session: event.newValue || undefined },
          replace: true,
        })
      }
    }
    window.addEventListener("storage", follow)
    return () => window.removeEventListener("storage", follow)
  }, [mode, storageKey, navigate])
  useEffect(() => {
    stream.stop()
    if (restart) replacementSession.current = ""
    setSessionId("")
    setMessages([])
    if (!preserveText.current) setText(prompt ?? "")
    initialize.mutate(selectedSession)
    return () => {
      initialization.current++
    }
  }, [initialize.mutate, selectedSession, restart, prompt, stream.stop])
  const busy = initialize.isPending || send.isPending
  useEffect(() => {
    if (messages.length || stream.text || stream.tools.length)
      bottom.current?.scrollIntoView({ block: "end" })
  }, [messages, stream.text, stream.tools])
  async function sendText(value: string, attachments: AttachmentPublic[] = []) {
    setMessages((previous) => [
      ...previous,
      {
        role: "user",
        payload: { text: value, attachments },
        created_at: new Date().toISOString(),
      },
    ])
    setText("")
    const result = await send.mutateAsync({
      text: value,
      attachment_ids: attachments.map((item) => item.id),
    })
    return !!result
  }
  const cards = messages.flatMap(({ payload }) =>
    "messages" in payload ? payload.messages : [],
  )
  const requestHistory = requestCardHistory(cards)
  const latestProjectDraft = cards
    .reverse()
    .find((message) => message.type === "project_draft_card")
  return (
    <>
      <div
        className="min-h-0 flex-1 space-y-6 overflow-y-auto p-4 text-sm"
        role="log"
        aria-label="工作助手对话"
        aria-busy={busy}
      >
        {messages.length === 0 && (
          <p className="mx-auto max-w-3xl text-muted-foreground leading-relaxed">
            你好，我可以帮你查看或更新画像、核对初始草稿、补充经历，也可以查询申请进度。
            {managedDepartmentIds.length > 0 &&
              "也支持查看团队画像与处理审批。"}
            <br />
            {description}
            提交前请核对预览卡片。未开启 AI 模型时，可直接前往
            <Link
              className="underline"
              to="/employees/$employeeId"
              params={{ employeeId }}
            >
              「我的画像」
            </Link>
            编辑。
          </p>
        )}
        <ChatMessages
          messages={messages}
          onDownload={async (id) =>
            (
              await BotService.downloadAttachment({
                query,
                path: { session_id: sessionId, attachment_id: id },
              })
            ).data
          }
          text={stream.text}
          tools={stream.tools}
          stopped={stream.stopped}
          renderMessage={(entry, index) => (
            <div key={index} className="space-y-4 break-words">
              {"messages" in entry.payload
                ? entry.payload.messages.map((message, i) => (
                    <BotMessage
                      dictionary={dictionary.data}
                      key={i}
                      message={message}
                      laterRequest={
                        message.type === "preview_card" ||
                        message.type === "status_card"
                          ? requestHistory.get(message)
                          : undefined
                      }
                      disabled={
                        busy ||
                        conflict === "preview" ||
                        (message.type === "project_draft_card" &&
                          message !== latestProjectDraft)
                      }
                      onAction={(action) => send.mutate(action)}
                    />
                  ))
                : entry.payload.text}
            </div>
          )}
        />
        {busy && (
          <p
            role="status"
            className="flex items-center gap-2 text-muted-foreground"
          >
            <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
            {initialize.isPending ? "加载对话中…" : "正在思考…"}
          </p>
        )}
        <SetupError
          error={initialize.error ?? (conflict ? null : send.error)}
        />
        {expired && <p role="status">原对话已过期，已为你开启新对话</p>}
        {initialize.isError && (
          <Button variant="outline" onClick={() => initialize.mutate()}>
            重试
          </Button>
        )}
        {conflict === "preview" && (
          <div role="alert" className="space-y-2">
            <p>内容已更新，请重新预览后再确认</p>
            <Button
              disabled={busy}
              onClick={() => sendText("请重新预览最新的画像请求")}
            >
              重新预览
            </Button>
          </div>
        )}
        {conflict === "withdraw" && (
          <p role="alert">请求状态已变化，这张卡片无法撤回。请查看当前状态。</p>
        )}
        <div ref={bottom} />
      </div>
      <ChatComposer
        key={sessionId}
        draftKey={sessionId ? `${storageKey}:${sessionId}` : undefined}
        id="bot-input"
        label="发给助手的消息"
        placeholder="说说你的经历，或想查询什么…"
        text={text}
        onChange={setText}
        onSend={(attachments) =>
          sendText(text.trim() || "请查看附件", attachments)
        }
        onUpload={async (file) =>
          (
            await BotService.uploadAttachment({
              query,
              path: { session_id: sessionId },
              body: { file },
            })
          ).data
        }
        busy={stream.isPending}
        disabled={busy || !sessionId}
        onStop={stream.stop}
      />
      <p className="px-4 pb-4 text-xs text-muted-foreground">
        确认过的陈述作为受限自述证据保存。
      </p>
    </>
  )
}
