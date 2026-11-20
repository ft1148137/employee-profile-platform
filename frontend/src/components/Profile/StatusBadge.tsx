import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"
import { labels, unknownStatus } from "./labels"

type StatusValue<T extends keyof typeof labels> = T extends keyof typeof labels
  ? keyof (typeof labels)[T]
  : never

export function StatusBadge<T extends keyof typeof labels>({
  kind,
  value,
}: {
  kind: T
  value: StatusValue<T> | null | undefined
}) {
  const options: Record<string, { label: string; className: string }> =
    labels[kind]
  const status = value == null ? undefined : options[String(value)]
  return (
    <Badge
      variant="outline"
      className={cn(
        "workspace:border-current/20 workspace:py-0 workspace:leading-5",
        status?.className ??
          "workspace:bg-muted workspace:text-muted-foreground",
      )}
    >
      {(status ?? unknownStatus).label}
    </Badge>
  )
}
