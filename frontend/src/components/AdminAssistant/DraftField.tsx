import type { ReactNode } from "react"

export function DraftField({
  changed,
  children,
  label = "助手建议",
}: {
  changed: boolean
  children: ReactNode
  label?: string
}) {
  return (
    <div
      data-assistant-suggested={changed || undefined}
      className={
        changed
          ? "space-y-2 rounded-lg border border-primary/50 bg-primary/10 p-3"
          : "space-y-2"
      }
    >
      {changed && <p className="text-xs font-medium text-primary">{label}</p>}
      {children}
    </div>
  )
}
