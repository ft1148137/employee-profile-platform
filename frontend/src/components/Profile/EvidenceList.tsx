import { useQuery } from "@tanstack/react-query"
import { FileText, LockKeyhole, MessageSquare, Ticket } from "lucide-react"
import { useState } from "react"
import {
  type EvidenceKind,
  type EvidenceRelation,
  EvidenceService,
} from "@/client"
import { Button } from "@/components/ui/button"
import { dateLabel } from "./fields"
import { labels } from "./labels"
import { ProfileError, ProfileLoading } from "./QueryState"

export const relationLabels = {
  supports: "支持",
  contradicts: "反对",
  context: "背景",
}
const icons = {
  work_record: Ticket,
  self_report: FileText,
  manager_review: MessageSquare,
}

type EvidenceRow = {
  id: string
  title: string
  kind: EvidenceKind
  occurred_at?: string | null
  redacted?: boolean
  relation?: EvidenceRelation
}
export function EvidenceRows({
  items,
  onSelect,
}: {
  items: EvidenceRow[]
  onSelect: (id: string) => void
}) {
  if (!items.length)
    return <p className="py-6 text-sm text-muted-foreground">暂无证据。</p>
  return (
    <ul className="divide-y">
      {items.map((item) => {
        const Icon = item.redacted ? LockKeyhole : icons[item.kind]
        return (
          <li key={item.id}>
            <button
              type="button"
              className="flex w-full items-start gap-3 rounded-md px-2 py-4 text-left hover:bg-muted/40 focus-visible:outline-ring"
              onClick={() => onSelect(item.id)}
            >
              <Icon
                aria-hidden="true"
                className="mt-1 size-4 shrink-0 text-muted-foreground"
              />
              <span className="min-w-0 flex-1">
                <span className="block break-words text-sm font-medium">
                  {item.title}
                </span>
                <span className="mt-1 block text-xs text-muted-foreground">
                  {labels.evidence_kind[item.kind].label} ·{" "}
                  {dateLabel(item.occurred_at)}
                  {item.redacted && " · 受限内容"}
                </span>
              </span>
              {item.relation && (
                <span
                  className={`text-xs ${item.relation === "contradicts" ? "text-rose-700 dark:text-rose-300" : "text-muted-foreground"}`}
                >
                  {relationLabels[item.relation]}
                </span>
              )}
            </button>
          </li>
        )
      })}
    </ul>
  )
}

export function EvidenceList({
  orgId,
  employeeId,
  onSelect,
}: {
  orgId: string
  employeeId: string
  onSelect: (id: string) => void
}) {
  const [kind, setKind] = useState<EvidenceKind | undefined>()
  const [skip, setSkip] = useState(0)
  const query = useQuery({
    queryKey: ["evidence", orgId, employeeId, kind, skip],
    queryFn: async () =>
      (
        await EvidenceService.readEmployeeEvidence({
          path: { org_id: orgId, employee_id: employeeId },
          query: { kind, skip, limit: 100, include_inactive: true },
        })
      ).data,
  })
  return (
    <div className="space-y-4">
      <label className="flex items-center gap-3 text-sm">
        证据类型
        <select
          aria-label="证据类型"
          className="rounded-md border bg-background p-2"
          value={kind ?? ""}
          onChange={(event) => {
            setKind(
              event.target.value
                ? (event.target.value as EvidenceKind)
                : undefined,
            )
            setSkip(0)
          }}
        >
          <option value="">全部类型</option>
          {Object.entries(labels.evidence_kind).map(([key, item]) => (
            <option key={key} value={key}>
              {item.label}
            </option>
          ))}
        </select>
      </label>
      {query.isPending ? (
        <ProfileLoading />
      ) : query.isError ? (
        <ProfileError retry={() => query.refetch()} />
      ) : (
        <>
          <EvidenceRows items={query.data.data} onSelect={onSelect} />
          {query.data.count > 100 && (
            <div className="flex items-center gap-4">
              <Button
                variant="outline"
                disabled={skip === 0}
                onClick={() => setSkip(skip - 100)}
              >
                上一页
              </Button>
              <span>共 {query.data.count} 条</span>
              <Button
                variant="outline"
                disabled={skip + 100 >= query.data.count}
                onClick={() => setSkip(skip + 100)}
              >
                下一页
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  )
}
