import type { ReactNode } from "react"
import type { AdminSessionMessage, SessionMessage } from "@/client"
import { ChatAttachment } from "./ChatAttachment"
import type { ToolProgress } from "./useStreamingTurn"

export function ChatMessages<
  Message extends {
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
  messages: (Message & { tools?: ToolProgress[] })[]
  renderMessage: (message: Message, index: number) => ReactNode
  text: string
  tools: ToolProgress[]
  stopped: boolean
  onDownload: (id: string) => Promise<Blob>
}) {
  return (
    <>
      {messages.map((message, index) => (
        <div key={index}>
          {renderMessage(message, index)}
          {"attachments" in message.payload &&
            (message.payload.attachments ?? []).map((attachment) => (
              <ChatAttachment
                key={attachment.id}
                attachment={attachment}
                onDownload={onDownload}
              />
            ))}
          <ToolCalls tools={message.tools ?? []} />
        </div>
      ))}
      <ToolCalls tools={tools} />
      {text && <p className="whitespace-pre-wrap">{text}</p>}
      {stopped && <p role="status">已停止</p>}
    </>
  )
}

function ToolCalls({ tools }: { tools: ToolProgress[] }) {
  return (
    <>
      {!!tools.length && (
        <details className="rounded-lg border p-3">
          <summary className="cursor-pointer">
            调用了 {tools.filter((tool) => tool.phase === "start").length}{" "}
            个功能
          </summary>
          <ul className="space-y-2 pt-2">
            {tools.map(
              (tool, index) =>
                tool.phase === "start" && (
                  <li key={index}>
                    {tool.name}：
                    {tools[index + 1]?.phase !== "end"
                      ? "进行中"
                      : tools[index + 1].ok
                        ? "成功"
                        : "失败"}
                  </li>
                ),
            )}
          </ul>
        </details>
      )}
    </>
  )
}
