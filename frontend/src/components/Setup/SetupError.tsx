import { AxiosError } from "axios"
import { handleError } from "@/utils"

export function SetupError({ error }: { error: Error | null }) {
  if (!error) return null
  let messages: string[] = []
  handleError.call((message) => {
    messages = [message]
  }, error)
  if (error instanceof AxiosError) {
    const detail = error.response?.data?.detail
    if (typeof detail === "string") messages = [detail]
    else if (detail && typeof detail.message === "string") {
      messages = [detail.message]
      if (Array.isArray(detail.blockers))
        messages.push(
          ...detail.blockers.filter(
            (item: unknown): item is string => typeof item === "string",
          ),
        )
    }
  }
  return (
    <div
      role="alert"
      className="rounded-lg border border-destructive/40 p-4 text-sm text-destructive"
    >
      {messages.map((message) => (
        <p key={message}>{message}</p>
      ))}
    </div>
  )
}
