import { Link } from "@tanstack/react-router"
import { useState } from "react"
import type { ChangeRequestPublic, ProfileItem } from "@/client"
import { TopbarActions } from "@/components/Common/TopbarSlot"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { CapabilityDetailDialog } from "./CapabilityDetailDialog"
import { CapabilityPicker } from "./CapabilityPicker"
import { dateLabel, levelLabel } from "./fields"
import { labels } from "./labels"

function assessmentLabel(item: ProfileItem) {
  return item.status === "confirmed" && item.claim && !item.claim.level
    ? "ungraded"
    : item.status
}

const filters = [
  ["all", "全部能力"],
  ...Object.entries(labels.assessment_status).map(([status, { label }]) => [
    status,
    label,
  ]),
  ["ungraded", "未定级"],
]

export function CapabilityList({
  items,
  onEvidence,
  onViewVersions,
  pendingCapabilityIds,
  onEdit,
  departmentConfigured,
  canConfigureDepartment,
  compact = false,
  directEdit = false,
  appeals,
}: {
  directEdit?: boolean
  compact?: boolean
  items: ProfileItem[]
  onEvidence: (id: string) => void
  onViewVersions?: () => void
  pendingCapabilityIds?: Set<string>
  onEdit?: (item: ProfileItem, retract?: boolean) => void
  departmentConfigured?: boolean
  canConfigureDepartment?: boolean
  appeals?: { orgId: string; requests: ChangeRequestPublic[] }
}) {
  const [added, setAdded] = useState(new Set<string>())
  const [picking, setPicking] = useState(false)
  const [filter, setFilter] = useState("all")
  const rejectedCapabilityKeys = new Set(
    appeals?.requests.flatMap((request) =>
      (request.items ?? [])
        .filter((item) => item.decision === "rejected")
        .map((item) => item.capability.key),
    ),
  )
  // Historical snapshots omit departmentConfigured and keep every frozen item.
  const visible = items.filter(
    (item) =>
      departmentConfigured === undefined ||
      (departmentConfigured && item.in_department) ||
      item.status !== "unknown" ||
      pendingCapabilityIds?.has(item.capability.id) ||
      rejectedCapabilityKeys.has(item.capability.key) ||
      added.has(item.capability.id),
  )
  const available = items.filter((item) => !item.retired)
  const counts = new Map<string, number>()
  const groups = new Map<string, ProfileItem[]>()
  for (const item of visible) {
    const status = assessmentLabel(item)
    counts.set(status, (counts.get(status) ?? 0) + 1)
    const ungraded = item.claim && !item.claim.level
    if (ungraded && status !== "ungraded")
      counts.set("ungraded", (counts.get("ungraded") ?? 0) + 1)
    if (
      filter !== "all" &&
      filter !== status &&
      !(filter === "ungraded" && ungraded)
    )
      continue
    const category = item.retired
      ? "历史能力（已移出词典）"
      : (item.capability.category ?? "其他能力")
    groups.set(category, [...(groups.get(category) ?? []), item])
  }
  return (
    <div className="space-y-3">
      {onEdit && (
        <TopbarActions>
          <Button size="sm" onClick={() => setPicking(true)}>
            添加能力
          </Button>
        </TopbarActions>
      )}
      {departmentConfigured === false && (
        <p className="text-xs text-muted-foreground">
          本部门暂未配置常用能力
          {canConfigureDepartment && (
            <Link to="/org-chart" className="ml-2 text-primary underline">
              去组织架构设置
            </Link>
          )}
        </p>
      )}
      <section className="flex flex-wrap gap-1.5" aria-label="能力状态统计">
        {filters.map(([status, label]) => (
          <Button
            key={status}
            size="sm"
            variant="outline"
            aria-pressed={filter === status}
            className="h-7 gap-1 rounded-full border-surface-border bg-surface px-2.5 text-xs text-muted-foreground shadow-none aria-pressed:border-primary aria-pressed:bg-primary/10 aria-pressed:text-primary"
            onClick={() => setFilter(status)}
          >
            {label}{" "}
            <span className="tabular-nums">
              {status === "all" ? visible.length : (counts.get(status) ?? 0)}
            </span>
          </Button>
        ))}
      </section>
      {groups.size === 0 && (
        <p className="rounded-lg border border-surface-border bg-surface p-4 text-sm text-muted-foreground">
          {filter === "all" ? "暂无可显示的能力。" : "此状态下暂无能力。"}
        </p>
      )}
      {Array.from(groups, ([category, entries]) => (
        <section key={category} className="space-y-2">
          <h2 className="flex items-center gap-2 text-xs font-semibold text-muted-foreground">
            {category}
            <span className="rounded-full bg-muted px-1.5 tabular-nums">
              {entries.length}
            </span>
          </h2>
          <div className="grid gap-2.5 md:grid-cols-2 xl:grid-cols-3">
            {entries.map((item) => {
              const { capability, claim } = item
              const evidenceCount = claim?.evidence?.length ?? 0
              const assessment = assessmentLabel(item)
              const badge =
                assessment === "ungraded"
                  ? {
                      label: "未定级",
                      className: "bg-muted text-muted-foreground",
                    }
                  : labels.assessment_status[item.status]
              const level = claim?.level
                ? item.level_adjusted
                  ? "等级已调整"
                  : levelLabel(capability.level_rubric, claim.level)
                : claim && assessment !== "ungraded"
                  ? "未定级"
                  : null
              return (
                <article
                  key={capability.id}
                  aria-label={capability.name}
                  className="relative flex min-w-0 flex-col gap-2 rounded-lg border border-surface-border bg-surface p-3 transition-colors hover:border-primary/45"
                >
                  <div className="flex flex-wrap items-start justify-between gap-x-2 gap-y-1">
                    <h3 className="min-w-0 font-semibold text-sm">
                      <CapabilityDetailDialog
                        item={item}
                        compact={compact}
                        directEdit={directEdit}
                        onEdit={onEdit}
                        onEvidence={onEvidence}
                        onViewVersions={onViewVersions}
                        appeals={appeals}
                      />
                    </h3>
                    {level && (
                      <Badge
                        variant="outline"
                        className="max-w-full whitespace-normal [overflow-wrap:anywhere]"
                      >
                        {level}
                      </Badge>
                    )}
                  </div>
                  {claim ? (
                    <p className="line-clamp-2 text-xs leading-relaxed text-muted-foreground [overflow-wrap:anywhere]">
                      {claim.statement}
                    </p>
                  ) : (
                    <p className="text-xs text-muted-foreground">
                      暂无记录（不代表不具备该能力）
                    </p>
                  )}
                  <div className="mt-auto flex flex-wrap items-center gap-1.5 border-t border-surface-border pt-2 text-xs text-muted-foreground">
                    <Badge variant="outline" className={badge.className}>
                      {badge.label}
                    </Badge>
                    {departmentConfigured !== undefined &&
                      !item.in_department &&
                      item.status !== "unknown" && (
                        <Badge variant="outline">其他能力</Badge>
                      )}
                    {!compact && item.evidence_stale && (
                      <Badge variant="outline">证据较旧</Badge>
                    )}
                    {pendingCapabilityIds?.has(capability.id) && (
                      <span>有待处理更新</span>
                    )}
                    {(evidenceCount > 0 || claim?.created_at) && (
                      <span className="ml-auto tabular-nums">
                        {evidenceCount > 0 && `${evidenceCount} 条证据`}
                        {evidenceCount > 0 && claim?.created_at && " · "}
                        {claim?.created_at && dateLabel(claim.created_at)}
                      </span>
                    )}
                  </div>
                </article>
              )
            })}
          </div>
        </section>
      ))}
      {picking && onEdit && (
        <CapabilityPicker
          capabilities={available.map((item) => ({
            ...item.capability,
            profileStatus: visible.includes(item)
              ? item.status === "unknown" && !item.claim
                ? "unfilled"
                : "recorded"
              : undefined,
          }))}
          onClose={() => setPicking(false)}
          onSelect={(key) => {
            const item = available.find((item) => item.capability.key === key)!
            setAdded((old) => new Set([...old, item.capability.id]))
            setPicking(false)
            setFilter("all")
            onEdit(item)
          }}
        />
      )}
    </div>
  )
}
