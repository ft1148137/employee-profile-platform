import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link, useNavigate } from "@tanstack/react-router"
import { AxiosError } from "axios"
import { Bot, LoaderCircle } from "lucide-react"
import { useEffect, useRef, useState } from "react"
import {
  type AttachmentPublic,
  BotService,
  EmployeeRequestsService,
  type SessionMessage,
  type TurnInput,
  type TurnResponse,
} from "@/client"
import { AssistantLayout } from "@/components/Chat/AssistantLayout"
import { ChatComposer, readChatDraft } from "@/components/Chat/ChatComposer"
import { ChatMessages } from "@/components/Chat/ChatMessages"
import { useStreamingTurn } from "@/components/Chat/useStreamingTurn"
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
import {
  rememberConversation,
  useCurrentConversation,
} from "@/hooks/useCurrentConversation"
import { useOrg } from "@/hooks/useOrg"
import { type BotAction, BotMessage } from "./BotMessage"
import { SessionHistory, useBotConversations } from "./SessionHistory"

export function BotPanel() {
  const { org, employeeId } = useOrg()
  const { user } = useAuth()
  if (!org || !employeeId || !user) return null
  return (
    <BotConversation
      key={`${user.id}:${org.id}:${employeeId}`}
      orgId={org.id}
      employeeId={employeeId}
      storageKey={`bot-session:${user.id}:${org.id}:${employeeId}`}
    />
  )
}

export function BotConversation({
  orgId,
  employeeId,
  storageKey,
  fullPage = false,
  selectedSession,
  prompt,
}: {
  orgId: string
  employeeId: string
  storageKey: string
  fullPage?: boolean
  selectedSession?: string
  prompt?: string
}) {
  const { managedDepartmentIds } = useOrg()
  const description = `${managedDepartmentIds.length ? "可以操作你本人和你管辖部门员工的画像" : "只操作你本人的画像"}。画像不用于绩效、薪酬和晋升。聊天原文默认不保存；未开启保留时，会话闲置两小时后清除。`
  const [open, setOpen] = useState(false)
  const client = useQueryClient()
  const navigate = useNavigate()
  const history = useBotConversations(fullPage ? orgId : undefined)
  const [sessionId, setSessionId] = useState("")
  const remembered = useCurrentConversation(storageKey)
  const [messages, setMessages] = useState<
    (Pick<SessionMessage, "role" | "payload"> &
      Partial<Pick<SessionMessage, "created_at">>)[]
  >([])
  const [text, setText] = useState(prompt ?? "")
  const [expired, setExpired] = useState(false)
  const replacementSession = useRef("")
  const [conflict, setConflict] = useState(false)
  const bottom = useRef<HTMLDivElement>(null)
  const selection = useRef(selectedSession)
  selection.current = selectedSession
  const initialization = useRef(0)
  const preserveText = useRef(false)
  const query = { org_id: orgId }
  const dictionary = useQuery({
    queryKey: ["my-capabilities", orgId],
    queryFn: async () =>
      (await EmployeeRequestsService.readMyCapabilities({ path: query })).data,
    enabled: fullPage || open,
  })
  const initialize = useMutation({
    onMutate: () => ++initialization.current,
    mutationFn: async (selection?: string) => {
      let expired = false
      let id =
        selection === "new"
          ? null
          : (selection ?? localStorage.getItem(storageKey))
      if (id) {
        try {
          const messages = (
            await BotService.listMessages({ query, path: { session_id: id } })
          ).data
          return { id, messages, expired }
        } catch (error) {
          if (!(error instanceof AxiosError) || error.response?.status !== 404)
            throw error
          expired = !!selection
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
      if (fullPage && data.expired)
        navigate({
          to: "/assistant",
          search: { session: data.id, prompt },
          replace: true,
        })
      if (data.expired)
        client.invalidateQueries({
          queryKey: ["conversation-history", orgId, false],
        })
      setConflict(false)
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
    onMutate: () => selectedSession,
    mutationFn: async (input: TurnInput | BotAction) => {
      const options = { query, path: { session_id: sessionId } }
      return "text" in input
        ? stream.mutateAsync(input)
        : {
            response: (await BotService.act({ ...options, body: input })).data,
            created_at: new Date().toISOString(),
          }
    },
    onSuccess: (result, _input, startedAt) => {
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
      setConflict(false)
      client.invalidateQueries({ queryKey: ["conversation-history", orgId] })
      client.invalidateQueries({ queryKey: ["change-requests", orgId] })
      client.invalidateQueries({ queryKey: ["inbox", orgId] })
      client.invalidateQueries({ queryKey: ["profile", orgId] })
    },
    onError: (error, _input, startedAt) => {
      if (selection.current !== startedAt) return
      if (error instanceof AxiosError && error.response?.status === 409)
        setConflict(true)
    },
  })
  useEffect(() => {
    const follow = (event: StorageEvent) => {
      if (fullPage && event.key === storageKey && event.newValue) {
        preserveText.current = true
        navigate({
          to: "/assistant",
          search: { session: event.newValue || undefined },
          replace: true,
        })
      }
    }
    window.addEventListener("storage", follow)
    return () => window.removeEventListener("storage", follow)
  }, [fullPage, storageKey, navigate])
  useEffect(() => {
    if (!fullPage) return
    stream.stop()
    setSessionId("")
    setMessages([])
    if (!preserveText.current) setText(prompt ?? "")
    initialize.mutate(selectedSession)
  }, [fullPage, initialize.mutate, selectedSession, prompt, stream.stop])
  const busy = initialize.isPending || send.isPending
  useEffect(() => {
    if (
      !fullPage &&
      open &&
      sessionId &&
      remembered &&
      remembered !== sessionId &&
      !busy
    )
      initialize.mutate()
  }, [fullPage, open, sessionId, remembered, busy, initialize.mutate])
  useEffect(() => {
    if (
      (fullPage || open) &&
      (messages.length || stream.text || stream.tools.length)
    )
      bottom.current?.scrollIntoView({ block: "end" })
  }, [fullPage, open, messages, stream.text, stream.tools])
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
  const conversations = (close?: () => void) => (
    <SessionHistory
      orgId={orgId}
      currentId={sessionId}
      disabled={busy}
      sidebar={fullPage}
      onNew={() => {
        if (fullPage) {
          rememberConversation(storageKey, "")
          setText("")
          if (selectedSession || prompt)
            navigate({ to: "/assistant", search: {} })
          else initialize.mutate("new")
        } else {
          setText("")
          initialize.mutate("new")
        }
        close?.()
      }}
      onSelect={(id) => {
        if (fullPage) navigate({ to: "/assistant", search: { session: id } })
        else {
          setText("")
          initialize.mutate(id)
        }
        close?.()
      }}
    />
  )
  const content = (
    <>
      {!fullPage && conversations()}
      <div
        className="min-h-0 flex-1 space-y-6 overflow-y-auto p-4 text-sm"
        role="log"
        aria-label="机器人对话"
        aria-busy={busy}
      >
        {(!fullPage || messages.length === 0) && (
          <p className="mx-auto max-w-3xl text-muted-foreground leading-relaxed">
            {fullPage && description}
            可以请我查看画像、过目初始画像、补充经历、查进度或回答问题。提交前请核对预览卡片。没有启用模型时，请到
            <Link
              className="underline"
              to="/employees/$employeeId"
              params={{ employeeId }}
            >
              {fullPage ? "打开画像页" : "我的画像"}
            </Link>
            直接修改。
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
                      disabled={busy || conflict}
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
            {initialize.isPending ? "正在恢复对话…" : "正在处理，请稍候…"}
          </p>
        )}
        <SetupError error={initialize.error ?? send.error} />
        {expired && <p role="status">这段对话已过期被清除，已为你新开对话</p>}
        {initialize.isError && (
          <Button variant="outline" onClick={() => initialize.mutate()}>
            重试加载对话
          </Button>
        )}
        {conflict && (
          <div role="alert" className="space-y-2">
            <p>内容已变化，请重新预览后再确认。</p>
            <Button
              disabled={busy}
              onClick={() => sendText("请重新预览最新的画像请求")}
            >
              重新预览最新内容
            </Button>
          </div>
        )}
        <div ref={bottom} />
      </div>
      <ChatComposer
        key={sessionId}
        draftKey={sessionId ? `${storageKey}:${sessionId}` : undefined}
        id="bot-input"
        label="发给机器人的消息"
        placeholder="给画像机器人发消息…"
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
  if (!fullPage)
    return (
      <Sheet
        open={open}
        onOpenChange={(value) => {
          setOpen(value)
          if (value && !sessionId && !initialize.isPending) initialize.mutate()
        }}
      >
        <SheetTrigger asChild>
          <Button
            className="fixed bottom-5 right-5 z-20 rounded-full shadow-lg"
            aria-label="打开画像机器人"
          >
            <Bot aria-hidden="true" />
            画像机器人
          </Button>
        </SheetTrigger>
        <SheetContent className="inset-0 h-dvh w-full gap-0 sm:inset-auto sm:bottom-5 sm:right-5 sm:h-[min(740px,calc(100dvh-40px))] sm:w-[440px] sm:max-w-[440px] sm:rounded-2xl sm:border">
          <SheetHeader className="shrink-0 border-b pr-12">
            <SheetTitle>我的画像机器人</SheetTitle>
            <SheetDescription>{description}</SheetDescription>
          </SheetHeader>
          {content}
        </SheetContent>
      </Sheet>
    )
  const title =
    history.data?.data.find((entry) => entry.id === sessionId)?.preview ||
    "新对话"
  return (
    <AssistantLayout label="助手" title={title} conversations={conversations}>
      <div className="flex h-full min-h-0 flex-col">{content}</div>
    </AssistantLayout>
  )
}
