import { useMutation } from "@tanstack/react-query"
import { useLocation } from "@tanstack/react-router"
import { AxiosError } from "axios"
import { useState } from "react"
import {
  AdminAssistantService,
  type AdminSessionMessage,
  type AdminTurnInput,
} from "@/client"
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

function AdminAssistantPanel({
  orgId,
  storageKey,
  page,
}: {
  orgId: string
  storageKey: string
  page: AdminTurnInput["page"]
}) {
  const [open, setOpen] = useState(false)
  const [sessionId, setSessionId] = useState("")
  const [messages, setMessages] = useState<
    Pick<AdminSessionMessage, "role" | "payload">[]
  >([])
  const [text, setText] = useState("")
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
        </SheetHeader>
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
          {busy && <p role="status">正在处理，请稍候…</p>}
          <SetupError error={initialize.error ?? send.error} />
          {initialize.isError && (
            <Button onClick={() => initialize.mutate(false)}>
              重试加载对话
            </Button>
          )}
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
      </SheetContent>
    </Sheet>
  )
}
