import { Link } from "@tanstack/react-router"
import { useState } from "react"
import type { ProfileItem } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { CapabilityPicker } from "./CapabilityPicker"
import { EvidenceRows } from "./EvidenceList"
import { dateLabel, levelLabel } from "./fields"
import { labels } from "./labels"
import { StatusBadge } from "./StatusBadge"

export function CapabilityList({
  items,
  onEvidence,
  pendingCapabilityIds,
  onEdit,
  departmentConfigured,
  canConfigureDepartment,
  compact = false,
}: {
  compact?: boolean
  items: ProfileItem[]
  onEvidence: (id: string) => void
  pendingCapabilityIds?: Set<string>
  onEdit?: (item: ProfileItem, retract?: boolean) => void
  departmentConfigured?: boolean
  canConfigureDepartment?: boolean
}) {
  const [added, setAdded] = useState(new Set<string>())
  const [picking, setPicking] = useState(false)
  // Historical snapshots omit departmentConfigured and keep every frozen item.
  const visible = items.filter(
    (item) =>
      departmentConfigured === undefined ||
      (departmentConfigured && item.in_department) ||
      item.status !== "unknown" ||
      pendingCapabilityIds?.has(item.capability.id) ||
      added.has(item.capability.id),
  )
  const available = items.filter(
    (item) => !item.retired && !visible.includes(item),
  )
  const groups = new Map<string, ProfileItem[]>()
  for (const item of visible) {
    const category = item.retired
      ? "已不在当前词典中的能力"
      : (item.capability.category ?? "其他能力")
    groups.set(category, [...(groups.get(category) ?? []), item])
  }
  return (
    <div className="space-y-8">
      {departmentConfigured === false && (
        <p className="rounded-lg border bg-muted/20 p-4 text-sm">
          本部门还没有设置对应能力
          {canConfigureDepartment && (
            <Link to="/org-chart" className="ml-3 text-primary underline">
              去组织架构设置
            </Link>
          )}
        </p>
      )}
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
            {visible.filter((item) => item.status === status).length}
          </span>
        ))}
      </section>
      {!visible.length && (
        <p className="py-8 text-muted-foreground">暂无可显示的能力。</p>
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
                compact={compact}
                onEvidence={onEvidence}
                pending={pendingCapabilityIds?.has(item.capability.id)}
                onEdit={!item.retired ? onEdit : undefined}
                otherDepartment={
                  departmentConfigured !== undefined &&
                  !item.in_department &&
                  item.status !== "unknown"
                }
              />
            ))}
          </div>
        </section>
      ))}
      {onEdit && (
        <Button variant="outline" onClick={() => setPicking(true)}>
          添加能力
        </Button>
      )}
      {picking && onEdit && (
        <CapabilityPicker
          capabilities={available.map((item) => item.capability)}
          onClose={() => setPicking(false)}
          onSelect={(key) => {
            const item = available.find((item) => item.capability.key === key)!
            setAdded((old) => new Set([...old, item.capability.id]))
            setPicking(false)
            onEdit(item)
          }}
        />
      )}
    </div>
  )
}

function ClaimCard({
  item: {
    capability,
    status,
    claim,
    evidence_stale,
    level_adjusted,
    original_level_description,
  },
  onEvidence,
  pending,
  onEdit,
  otherDepartment,
  compact = false,
}: {
  item: ProfileItem
  compact?: boolean
  onEvidence: (id: string) => void
  pending?: boolean
  onEdit?: (item: ProfileItem, retract?: boolean) => void
  otherDepartment?: boolean
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
        <div>
          <h3 className="font-semibold">{capability.name}</h3>
          {pending && (
            <p className="mt-1 text-xs text-muted-foreground">有待处理的改动</p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {otherDepartment && <Badge variant="outline">其他部门能力</Badge>}
          {!compact && evidence_stale && (
            <Badge variant="outline">证据较旧</Badge>
          )}
          <StatusBadge kind="assessment_status" value={status} />
        </div>
      </div>
      {compact ? (
        <p className="text-sm">
          等级：
          {claim?.level
            ? levelLabel(capability.level_rubric, claim.level)
            : "未给等级"}
        </p>
      ) : claim ? (
        <>
          <p className="leading-relaxed">{claim.statement}</p>
          <dl className="my-4 flex flex-wrap gap-x-5 gap-y-3 text-sm">
            {claim.level && (
              <div>
                <dt className="text-muted-foreground">等级</dt>
                <dd>
                  {level_adjusted
                    ? original_level_description
                    : levelLabel(capability.level_rubric, claim.level)}
                  {level_adjusted && (
                    <span className="ml-2 text-muted-foreground">
                      等级已调整
                    </span>
                  )}
                </dd>
              </div>
            )}
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
      {onEdit && (
        <div className="mt-4 flex flex-wrap gap-2">
          <Button
            variant="outline"
            onClick={() => onEdit({ capability, status, claim })}
          >
            {claim ? "修改" : "填写"}
          </Button>
          {claim && (
            <Button
              variant="ghost"
              onClick={() => onEdit({ capability, status, claim }, true)}
            >
              撤回这条判断
            </Button>
          )}
        </div>
      )}
    </article>
  )
}
