import { useMutation } from "@tanstack/react-query"
import { AxiosError, AxiosHeaders } from "axios"
import { useCallback, useEffect, useRef, useState } from "react"
import type { TextEvent, ToolEvent } from "@/client"

export type ToolProgress = ToolEvent

export function useStreamingTurn<Input, Response>(
  url: string,
  resolveUrl?: (signal: AbortSignal) => Promise<string>,
) {
  const controller = useRef<AbortController | null>(null)
  const stop = useCallback(() => controller.current?.abort(), [])
  const [text, setText] = useState("")
  const [tools, setTools] = useState<ToolProgress[]>([])
  const [stopped, setStopped] = useState(false)
  useEffect(() => {
    if (url) controller.current?.abort()
    setText("")
    setTools([])
    setStopped(false)
    return () => controller.current?.abort()
  }, [url])
  const mutation = useMutation({
    mutationFn: async (body: Input) => {
      const startedAt = new Date().toISOString()
      const abort = new AbortController()
      controller.current = abort
      setText("")
      setTools([])
      setStopped(false)
      try {
        const target = resolveUrl ? await resolveUrl(abort.signal) : url
        if (abort.signal.aborted) return undefined
        const response = await fetch(
          `${import.meta.env.VITE_API_URL ?? ""}${target}`,
          {
            method: "POST",
            headers: {
              Authorization: `Bearer ${localStorage.getItem("access_token") ?? ""}`,
              "Content-Type": "application/json",
            },
            body: JSON.stringify(body),
            signal: abort.signal,
          },
        )
        if (!response.ok)
          throw new AxiosError(
            "对话连接失败，请重试",
            undefined,
            undefined,
            undefined,
            {
              data: await response.json().catch(() => null),
              status: response.status,
              statusText: response.statusText,
              headers: {},
              config: { headers: new AxiosHeaders() },
            },
          )
        if (!response.body) throw new Error("对话连接中断，请重试")
        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        let pending = ""
        let final: Response | undefined
        const progress: ToolProgress[] = []
        try {
          while (true) {
            const { value, done } = await reader.read()
            pending += decoder.decode(value, { stream: !done })
            const lines = pending.split("\n")
            pending = lines.pop() ?? ""
            for (const line of lines) {
              if (!line.trim()) continue
              const event = JSON.parse(line) as
                | TextEvent
                | ToolEvent
                | { type: "final"; response: Response }
              if (abort.signal.aborted) return undefined
              if (event.type === "text") setText((text) => text + event.text)
              if (event.type === "tool") {
                progress.push(event)
                setTools([...progress])
              }
              if (event.type === "final") final = event.response
            }
            if (done) break
          }
          if (final === undefined || pending.trim())
            throw new Error("对话连接中断，请重试")
          return {
            response: final,
            startedAt,
            created_at: new Date().toISOString(),
          }
        } finally {
          await reader.cancel()
        }
      } catch (error) {
        if (!abort.signal.aborted) throw error
        return undefined
      } finally {
        if (controller.current === abort) {
          controller.current = null
          setText("")
          setTools([])
          if (abort.signal.aborted) {
            setStopped(true)
          }
        }
      }
    },
  })
  return {
    ...mutation,
    text,
    tools,
    stopped,
    stop,
  }
}
