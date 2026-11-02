import { Badge } from "@/components/ui/badge"
import { labels } from "./labels"

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
    <Badge variant="outline" className={status?.className}>
      {status?.label ?? "未知"}
    </Badge>
  )
}
