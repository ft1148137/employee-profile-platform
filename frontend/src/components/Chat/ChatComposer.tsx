import { useRef, useState } from "react"
import type { AttachmentPublic } from "@/client"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"

export function ChatComposer({
  id,
  label,
  placeholder,
  text,
  onChange,
  onSend,
  busy,
  disabled,
  onStop,
  onUpload,
}: {
  id: string
  label: string
  placeholder: string
  text: string
  onChange: (text: string) => void
  onSend: (attachments: AttachmentPublic[]) => Promise<boolean>
  busy: boolean
  disabled: boolean
  onStop: () => void
  onUpload: (file: File) => Promise<AttachmentPublic>
}) {
  const input = useRef<HTMLInputElement>(null)
  const [pending, setPending] = useState<
    { key: string; file: File; attachment?: AttachmentPublic; error?: Error }[]
  >([])
  const pendingRef = useRef(pending)
  const [error, setError] = useState("")
  const sending = useRef(false)
  const update = (items: typeof pending) => {
    pendingRef.current = items
    setPending(items)
  }
  const addFiles = (files: File[]) => {
    if (busy || disabled || sending.current || !files.length) return
    let problem = ""
    if (pendingRef.current.length + files.length > 5)
      problem = "每轮最多 5 个附件"
    for (const file of files) {
      const extension = file.name.split(".").pop()?.toLowerCase() ?? ""
      if (
        !/^(png|jpe?g|webp|gif|csv|xlsx|txt|md|json|docx|pdf)$/.test(extension)
      )
        problem = "不支持的附件类型"
      else if (
        !file.size ||
        file.size >
          (/^(png|jpe?g|webp|gif)$/.test(extension) ? 5 : 10) * 1024 * 1024
      )
        problem = "附件不能为空，图片最多 5 MiB，文件最多 10 MiB"
    }
    setError(problem)
    if (problem) return
    const added = files.map((file) => ({ key: crypto.randomUUID(), file }))
    update([...pendingRef.current, ...added])
    for (const item of added) {
      onUpload(item.file).then(
        (attachment) =>
          update(
            pendingRef.current.map((entry) =>
              entry.key === item.key ? { ...entry, attachment } : entry,
            ),
          ),
        (error: Error) =>
          update(
            pendingRef.current.map((entry) =>
              entry.key === item.key ? { ...entry, error } : entry,
            ),
          ),
      )
    }
  }
  const blocked = pending.some((item) => !item.attachment || item.error)
  const submit = async () => {
    if (
      busy ||
      disabled ||
      blocked ||
      sending.current ||
      (!text.trim() && !pending.length)
    )
      return
    sending.current = true
    try {
      if (
        await onSend(
          pending.flatMap((item) => (item.attachment ? [item.attachment] : [])),
        )
      ) {
        update([])
        setError("")
      }
    } catch {
      // The panel displays turn errors; keep attachments available for retry.
    } finally {
      sending.current = false
    }
  }
  return (
    <form
      className="shrink-0 space-y-3 border-t p-4"
      onDragOver={(event) => event.preventDefault()}
      onDrop={(event) => {
        event.preventDefault()
        addFiles(Array.from(event.dataTransfer.files))
      }}
      onSubmit={(event) => {
        event.preventDefault()
        submit()
      }}
    >
      <input
        ref={input}
        type="file"
        multiple
        hidden
        aria-label="选择对话附件"
        accept=".png,.jpg,.jpeg,.webp,.gif,.csv,.xlsx,.txt,.md,.json,.docx,.pdf"
        disabled={busy || disabled}
        onChange={(event) => {
          addFiles(Array.from(event.target.files ?? []))
          event.target.value = ""
        }}
      />
      <ul
        className="max-h-40 space-y-2 overflow-y-auto text-sm"
        aria-label="待发送附件"
      >
        {pending.map((item) => (
          <li key={item.key} className="rounded-lg border p-2">
            <p>
              {item.file.name} · {item.file.size.toLocaleString("zh-CN")} 字节
            </p>
            {!item.attachment && !item.error && <p role="status">上传中…</p>}
            {item.attachment && (
              <p role="status">
                已上传{item.attachment.notice && `：${item.attachment.notice}`}
              </p>
            )}
            <SetupError error={item.error ?? null} />
            <Button
              type="button"
              variant="ghost"
              disabled={busy}
              onClick={() =>
                update(
                  pendingRef.current.filter((entry) => entry.key !== item.key),
                )
              }
            >
              移除 {item.file.name}
            </Button>
          </li>
        ))}
      </ul>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      <label className="sr-only" htmlFor={id}>
        {label}
      </label>
      <textarea
        id={id}
        className="min-h-20 max-h-40 w-full rounded-lg border bg-background p-3 text-sm focus-visible:outline-ring"
        placeholder={placeholder}
        maxLength={10000}
        value={text}
        disabled={busy || disabled}
        onChange={(event) => onChange(event.target.value)}
        onPaste={(event) => {
          if (!event.clipboardData.files.length) return
          event.preventDefault()
          addFiles(Array.from(event.clipboardData.files))
        }}
        onKeyDown={(event) => {
          if (
            event.key === "Enter" &&
            !event.shiftKey &&
            !event.nativeEvent.isComposing
          ) {
            event.preventDefault()
            submit()
          }
        }}
      />
      <Button
        type="button"
        variant="outline"
        disabled={busy || disabled}
        onClick={() => input.current?.click()}
      >
        添加附件
      </Button>
      {busy ? (
        <Button type="button" onClick={onStop}>
          停止
        </Button>
      ) : (
        <Button
          disabled={disabled || blocked || (!text.trim() && !pending.length)}
        >
          发送
        </Button>
      )}
    </form>
  )
}
