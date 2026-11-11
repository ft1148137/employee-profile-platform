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
}: {
  id: string
  label: string
  placeholder: string
  text: string
  onChange: (text: string) => void
  onSend: () => void
  busy: boolean
  disabled: boolean
  onStop: () => void
}) {
  const submit = () => {
    if (!busy && !disabled && text.trim()) onSend()
  }
  return (
    <form
      className="shrink-0 space-y-3 border-t p-4"
      onSubmit={(event) => {
        event.preventDefault()
        submit()
      }}
    >
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
      {busy ? (
        <Button type="button" onClick={onStop}>
          停止
        </Button>
      ) : (
        <Button disabled={disabled || !text.trim()}>发送</Button>
      )}
    </form>
  )
}
