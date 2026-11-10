import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import { AxiosError } from "axios"
import { Bot, LoaderCircle } from "lucide-react"
import { useEffect, useRef, useState } from "react"
import { BotService, type SessionMessage } from "@/client"
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
import { SessionHistory } from "./SessionHistory"

export function BotPanel() {
  const { org, employeeId } = useOrg()
  const { user } = useAuth()
  if (!org || !employeeId || !user) return null
  return (
    <Panel
      key={`${user.id}:${org.id}:${employeeId}`}
      orgId={org.id}
      employeeId={employeeId}
      storageKey={`bot-session:${user.id}:${org.id}:${employeeId}`}
    />
  )
}

function Panel({
  orgId,
  employeeId,
  storageKey,
}: {
  orgId: string
  employeeId: string
  storageKey: string
}) {
  const client = useQueryClient()
  const [open, setOpen] = useState(false)
  const [sessionId, setSessionId] = useState("")
  const remembered = useCurrentConversation(storageKey)
  const [messages, setMessages] = useState<
    Pick<SessionMessage, "role" | "payload">[]
  >([])
  const [text, setText] = useState("")
  const [conflict, setConflict] = useState(false)
  const bottom = useRef<HTMLDivElement>(null)
  const query = { org_id: orgId }
  const initialize = useMutation({
    mutationFn: async (selection?: string) => {
      let id =
        selection === "new"
          ? null
          : (selection ?? localStorage.getItem(storageKey))
      if (id) {
        try {
          const messages = (
            await BotService.listMessages({ query, path: { session_id: id } })
          ).data
          return { id, messages }
        } catch (error) {
          if (!(error instanceof AxiosError) || error.response?.status !== 404)
            throw error
        }
      }
      id = (await BotService.createSession({ query })).data.session_id
      return { id, messages: [] }
    },
    onSuccess: (data) => {
      rememberConversation(storageKey, data.id)
      setText("")
      setConflict(false)
      send.reset()
      setSessionId(data.id)
      setMessages(data.messages)
    },
  })
  const send = useMutation({
    mutationFn: async (input: string | BotAction) => {
      const options = { query, path: { session_id: sessionId } }
      return typeof input === "string"
        ? BotService.turn({ ...options, body: { text: input }, timeout: 75000 })
        : BotService.act({ ...options, body: input })
    },
    onSuccess: ({ data }) => {
      setMessages((previous) => [
        ...previous,
        { role: "assistant", payload: data },
      ])
      setConflict(false)
      client.invalidateQueries({ queryKey: ["conversation-history", orgId] })
      client.invalidateQueries({ queryKey: ["change-requests", orgId] })
      client.invalidateQueries({ queryKey: ["inbox", orgId] })
      client.invalidateQueries({ queryKey: ["profile", orgId] })
    },
    onError: (error) => {
      if (error instanceof AxiosError && error.response?.status === 409)
        setConflict(true)
    },
  })
  const busy = initialize.isPending || send.isPending
  useEffect(() => {
    if (open && sessionId && remembered && remembered !== sessionId && !busy)
      initialize.mutate(remembered)
  }, [open, sessionId, remembered, busy, initialize.mutate])
  useEffect(() => {
    if (open && messages.length)
      bottom.current?.scrollIntoView({ block: "end" })
  }, [open, messages])
  function sendText(value: string) {
    setMessages((previous) => [
      ...previous,
      { role: "user", payload: { text: value } },
    ])
    setText("")
    send.mutate(value)
  }
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
          <SheetDescription>
            只操作你本人的画像。画像不用于绩效、薪酬和晋升。聊天原文默认不保存；未开启保留时，会话闲置两小时后清除。
          </SheetDescription>
        </SheetHeader>
        <SessionHistory
          orgId={orgId}
          currentId={sessionId}
          disabled={busy}
          onNew={() => initialize.mutate("new")}
          onSelect={(id) => initialize.mutate(id)}
        />
        <div
          className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4 text-sm"
          role="log"
          aria-label="机器人对话"
          aria-busy={busy}
        >
          <p className="rounded-xl bg-muted p-4 leading-relaxed">
            可以请我查看画像、过目初始画像、补充经历、查进度或回答问题。提交前请核对预览卡片。没有启用模型时，请到
            <Link
              className="underline"
              to="/employees/$employeeId"
              params={{ employeeId }}
            >
              我的画像
            </Link>
            直接修改。
          </p>
          {messages.map((entry, index) => (
            <div
              key={index}
              className={`space-y-3 break-words ${entry.role === "user" ? "ml-8 rounded-xl bg-primary/10 p-3 whitespace-pre-wrap" : "mr-1"}`}
            >
              <p className="text-xs text-muted-foreground">
                {entry.role === "user" ? "我" : "画像机器人"}
              </p>
              {"messages" in entry.payload
                ? entry.payload.messages.map((message, i) => (
                    <BotMessage
                      key={i}
                      message={message}
                      disabled={busy || conflict}
                      onAction={(action) => send.mutate(action)}
                    />
                  ))
                : entry.payload.text}
            </div>
          ))}
          {busy && (
            <p
              role="status"
              className="flex items-center gap-2 text-muted-foreground"
            >
              <LoaderCircle
                className="size-4 animate-spin"
                aria-hidden="true"
              />
              {initialize.isPending ? "正在恢复对话…" : "正在处理，请稍候…"}
            </p>
          )}
          <SetupError error={initialize.error ?? send.error} />
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
        <form
          className="shrink-0 space-y-3 border-t p-4"
          onSubmit={(event) => {
            event.preventDefault()
            if (text.trim()) sendText(text.trim())
          }}
        >
          <label className="sr-only" htmlFor="bot-input">
            发给机器人的消息
          </label>
          <textarea
            id="bot-input"
            className="min-h-20 max-h-40 w-full rounded-lg border bg-background p-3 text-sm focus-visible:outline-ring"
            placeholder="说说你想补充的经历…"
            maxLength={10000}
            value={text}
            onChange={(event) => setText(event.target.value)}
            disabled={busy || !sessionId}
          />
          <div className="flex items-center justify-between gap-3">
            <p className="text-xs text-muted-foreground">
              确认过的陈述作为受限自述证据保存。
            </p>
            <Button disabled={busy || !sessionId || !text.trim()}>发送</Button>
          </div>
        </form>
      </SheetContent>
    </Sheet>
  )
}
