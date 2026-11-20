import { Columns3, List } from "lucide-react"
import type { ReactNode } from "react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

export type PlanningView = "board" | "list"

export function PlanningViewToggle({
  view,
  onChange,
}: {
  view: PlanningView
  onChange: (view: PlanningView) => void
}) {
  return (
    <fieldset
      aria-label="显示方式"
      className="flex shrink-0 gap-0.5 rounded-md border border-surface-border bg-muted/50 p-0.5"
    >
      {(
        [
          ["board", "状态看板", Columns3],
          ["list", "紧凑列表", List],
        ] as const
      ).map(([value, label, Icon]) => (
        <Button
          key={value}
          size="sm"
          variant="ghost"
          className={cn(
            "h-7 gap-1 px-2",
            view === value && "bg-surface shadow-xs",
          )}
          aria-pressed={view === value}
          onClick={() => onChange(value)}
        >
          <Icon aria-hidden="true" />
          <span className="sr-only xl:not-sr-only">{label}</span>
        </Button>
      ))}
    </fieldset>
  )
}

export function PlanningBoard({
  groups,
}: {
  groups: { label: string; color: string; cards: ReactNode[] }[]
}) {
  return (
    <div className="grid items-start gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {groups.map(({ label, color, cards }) => (
        <section
          key={label}
          aria-label={label}
          className={cn(
            "min-w-0 overflow-hidden rounded-lg border border-surface-border border-t-3 bg-muted/50",
            color,
          )}
        >
          <h2 className="flex items-center justify-between gap-2 border-surface-border border-b bg-surface px-3 py-2 text-sm font-semibold">
            {label}
            <Badge variant="secondary">{cards.length}</Badge>
          </h2>
          <div className="space-y-2 p-2">
            {cards.length ? (
              cards
            ) : (
              <p className="px-1 py-2 text-xs text-muted-foreground">
                暂无记录
              </p>
            )}
          </div>
        </section>
      ))}
    </div>
  )
}

export const detailColumns =
  "grid min-w-0 items-start gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(17rem,30%)]"
export const detailAside =
  "min-w-0 space-y-3 xl:sticky xl:top-16 xl:max-h-[calc(100dvh-5rem)] xl:overflow-y-auto"
