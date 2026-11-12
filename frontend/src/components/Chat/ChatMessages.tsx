import { Check, Copy, LoaderCircle, X } from "lucide-react"
import { type ReactNode, useRef, useState } from "react"
import type { AdminSessionMessage, SessionMessage, ToolStep } from "@/client"
import { Button } from "@/components/ui/button"
import { ChatAttachment } from "./ChatAttachment"
import type { ToolProgress } from "./useStreamingTurn"

export function ChatMessages<
  Message extends {
    role: "user" | "assistant"
    created_at?: string
    payload: SessionMessage["payload"] | AdminSessionMessage["payload"]
  },
>({
  messages,
  renderMessage,
  text,
  tools,
  stopped,
  onDownload,
}: {
  messages: Message[]
  renderMessage: (message: Message, index: number) => ReactNode
  text: string
  tools: ToolProgress[]
  stopped: boolean
  onDownload: (id: string) => Promise<Blob>
}) {
  return (
    <div className="mx-auto w-full max-w-3xl space-y-8">
      {messages.map((message, index) => {
        const user = message.role === "user"
        const previous = messages
          .slice(0, index)
          .reverse()
          .find((entry) => entry.role === "user")
        const elapsed =
          Date.parse(message.created_at ?? "") -
          Date.parse(previous?.created_at ?? "")
        return (
          <div
            key={index}
            data-role={message.role}
            className={
              user
                ? "ml-auto w-fit max-w-[70%] space-y-2 rounded-2xl bg-muted px-4 py-3 whitespace-pre-wrap break-words"
                : "space-y-3 break-words leading-7"
            }
          >
            <ToolCalls
              steps={
                "steps" in message.payload ? (message.payload.steps ?? []) : []
              }
            />
            <Reply
              seconds={
                !user && Number.isFinite(elapsed) && elapsed >= 0
                  ? Math.max(1, Math.ceil(elapsed / 1000))
                  : undefined
              }
              assistant={!user}
            >
              {renderMessage(message, index)}
            </Reply>
            {"attachments" in message.payload &&
              (message.payload.attachments ?? []).map((attachment) => (
                <ChatAttachment
                  key={attachment.id}
                  attachment={attachment}
                  onDownload={onDownload}
                />
              ))}
          </div>
        )
      })}
      {(tools.length > 0 || text || stopped) && (
        <div data-role="assistant" className="space-y-3 leading-7">
          <ToolCalls
            steps={tools.flatMap((tool, index) =>
              tool.phase === "start"
                ? [
                    {
                      name: tool.name,
                      ok: tools[index + 1]?.ok ?? false,
                      running: tools[index + 1]?.phase !== "end",
                    },
                  ]
                : [],
            )}
          />
          {text && <p className="whitespace-pre-wrap break-words">{text}</p>}
          {stopped && (
            <p role="status" className="text-sm text-muted-foreground">
              已停止
            </p>
          )}
        </div>
      )}
    </div>
  )
}

function Reply({
  children,
  assistant,
  seconds,
}: {
  children: ReactNode
  assistant: boolean
  seconds?: number
}) {
  const body = useRef<HTMLDivElement>(null)
  const [error, setError] = useState(false)
  return (
    <>
      <div ref={body}>{children}</div>
      {assistant && (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          {seconds !== undefined && <span>{seconds} 秒内回复</span>}
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-7"
            aria-label="复制"
            onClick={async () => {
              try {
                await navigator.clipboard.writeText(
                  body.current?.textContent ?? "",
                )
                setError(false)
              } catch {
                setError(true)
              }
            }}
          >
            <Copy className="size-3.5" aria-hidden="true" />
          </Button>
          {error && <span role="status">复制失败，请重试</span>}
        </div>
      )}
    </>
  )
}

function ToolCalls({ steps }: { steps: (ToolStep & { running?: boolean })[] }) {
  if (!steps.length) return null
  const running = steps.find((step) => step.running)
  return (
    <details className="text-sm text-muted-foreground">
      <summary className="w-fit cursor-pointer py-1">
        {running ? `正在执行：${running.name}` : `${steps.length} 步`}
      </summary>
      <ul className="space-y-2 border-l pl-4 pt-2">
        {steps.map((step, index) => {
          const status = step.running ? "进行中" : step.ok ? "成功" : "失败"
          const Icon = step.running ? LoaderCircle : step.ok ? Check : X
          return (
            <li key={index} className="flex items-center gap-2">
              <Icon
                aria-hidden="true"
                className={`size-3.5 shrink-0 ${step.running ? "animate-spin" : ""}`}
              />
              <span>
                {step.name}：{status}
              </span>
            </li>
          )
        })}
      </ul>
    </details>
  )
}
