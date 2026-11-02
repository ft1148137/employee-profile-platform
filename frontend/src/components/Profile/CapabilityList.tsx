import type { ProfileItem } from "@/client"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { EvidenceRows } from "./EvidenceList"
import { dateLabel, levelLabel } from "./fields"
import { labels } from "./labels"
import { StatusBadge } from "./StatusBadge"

export function CapabilityList({
  items,
  onEvidence,
}: {
  items: ProfileItem[]
  onEvidence: (id: string) => void
}) {
  const groups = new Map<string, ProfileItem[]>()
  for (const item of items) {
    const category = item.capability.category ?? "其他能力"
    groups.set(category, [...(groups.get(category) ?? []), item])
  }
  return (
    <div className="space-y-8">
      <section
        className="flex flex-wrap gap-4 rounded-lg border bg-muted/20 p-4"
        aria-label="能力状态统计"
      >
        {Object.entries(labels.assessment_status).map(([status, label]) => (
          <span
            key={status}
            className={`rounded-md px-3 py-1 text-sm ${label.className}`}
          >
            {label.label}{" "}
            {items.filter((item) => item.status === status).length}
          </span>
        ))}
      </section>
      {!items.length && (
        <p className="py-8 text-muted-foreground">暂无能力定义。</p>
      )}
      {Array.from(groups, ([category, entries]) => (
        <section key={category} className="space-y-4">
          <h2 className="border-l-2 border-primary pl-3 text-lg font-semibold">
            {category}
          </h2>
          <div className="grid gap-4 xl:grid-cols-2">
            {entries.map((item) => (
              <ClaimCard
                key={item.capability.id}
                item={item}
                onEvidence={onEvidence}
              />
            ))}
          </div>
        </section>
      ))}
    </div>
  )
}

function ClaimCard({
  item: { capability, status, claim },
  onEvidence,
}: {
  item: ProfileItem
  onEvidence: (id: string) => void
}) {
  const actor = claim?.generated_by.actor_type
  const sources: Record<string, string> = {
    seed: "示例数据",
    agent: "助手",
    human: "人工",
    user: "人工",
  }
  const source = actor ? (sources[actor] ?? actor) : "—"
  return (
    <article
      aria-label={capability.name}
      className="rounded-xl border bg-card p-5 shadow-sm"
    >
      <div className="mb-4 flex items-center justify-between gap-3">
        <h3 className="font-semibold">{capability.name}</h3>
        <StatusBadge kind="assessment_status" value={status} />
      </div>
      {claim ? (
        <>
          <p className="leading-relaxed">{claim.statement}</p>
          <dl className="my-4 flex flex-wrap gap-x-5 gap-y-3 text-sm">
            {claim.level && (
              <div>
                <dt className="text-muted-foreground">等级</dt>
                <dd>{levelLabel(capability.level_rubric, claim.level)}</dd>
              </div>
            )}
            <div>
              <dt className="mb-1 text-muted-foreground">审核状态</dt>
              <dd>
                <StatusBadge
                  kind="claim_review_status"
                  value={claim.review_status}
                />
              </dd>
            </div>
            <div>
              <dt className="mb-1 text-muted-foreground">信心</dt>
              <dd>
                <Tooltip>
                  <TooltipTrigger className="cursor-help rounded focus-visible:outline-ring">
                    <StatusBadge kind="confidence" value={claim.confidence} />
                  </TooltipTrigger>
                  <TooltipContent>
                    粗粒度信心标签，不是经过校准的概率
                  </TooltipContent>
                </Tooltip>
              </dd>
            </div>
          </dl>
          <p className="rounded-md bg-muted/30 p-3 text-sm leading-relaxed text-muted-foreground">
            {claim.explanation ?? "暂无解释"}
          </p>
          <p className="my-3 text-xs text-muted-foreground">
            生成来源：{source}
            {claim.created_at && ` · ${dateLabel(claim.created_at)}`}
          </p>
          <details className="border-t pt-3">
            <summary className="cursor-pointer text-sm font-medium">
              相关证据（{claim.evidence?.length ?? 0}）
            </summary>
            <EvidenceRows
              items={(claim.evidence ?? []).map((evidence) => ({
                ...evidence,
                id: evidence.evidence_id,
              }))}
              onSelect={onEvidence}
            />
          </details>
        </>
      ) : (
        <p className="text-sm leading-relaxed text-muted-foreground">
          未知：暂无足够证据，不代表不具备这项能力
        </p>
      )}
    </article>
  )
}
