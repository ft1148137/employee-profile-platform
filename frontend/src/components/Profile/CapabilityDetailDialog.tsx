import { useState } from "react"
import type { ChangeRequestPublic, ProfileItem } from "@/client"
import { SideDrawer } from "@/components/Common/WorkbenchPrimitives"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { AppealItem } from "./AppealItem"
import { EvidenceRows } from "./EvidenceList"
import { dateLabel, levelLabel, record } from "./fields"
import { StatusBadge } from "./StatusBadge"

const sources: Record<string, string> = {
  seed: "示例数据",
  agent: "助手",
  human: "人工",
  user: "人工",
}

export function CapabilityDetailDialog({
  item,
  compact,
  directEdit,
  onEdit,
  onEvidence,
  onViewVersions,
  appeals,
}: {
  item: ProfileItem
  compact: boolean
  directEdit: boolean
  onEdit?: (item: ProfileItem, retract?: boolean) => void
  onEvidence: (id: string) => void
  onViewVersions?: () => void
  appeals?: { orgId: string; requests: ChangeRequestPublic[] }
}) {
  const [open, setOpen] = useState(false)
  const { capability, claim, status } = item
  const level = claim?.level
  const rubric = capability.level_rubric
  const levelEntry =
    level && record(rubric) && Array.isArray(rubric.levels)
      ? rubric.levels.find(
          (entry: unknown) => record(entry) && entry.key === level,
        )
      : undefined
  const levelDescription = item.level_adjusted
    ? item.original_level_description
    : record(levelEntry) && typeof levelEntry.description === "string"
      ? levelEntry.description
      : undefined
  const evidence = claim?.evidence ?? []
  const actor = claim?.generated_by.actor_type

  return (
    <SideDrawer
      title={capability.name}
      className="[overflow-wrap:anywhere]"
      open={open}
      onOpenChange={setOpen}
      trigger={
        <button
          type="button"
          className="text-left [overflow-wrap:anywhere] after:absolute after:inset-0 after:rounded-lg focus-visible:outline-none focus-visible:after:ring-2 focus-visible:after:ring-ring"
        >
          {capability.name}
        </button>
      }
    >
      <div className="flex min-h-full flex-col gap-4 text-sm [overflow-wrap:anywhere]">
        <div className="flex flex-wrap items-center gap-2">
          {(level || (claim && status !== "confirmed")) && (
            <Badge variant="secondary">
              {level ? levelLabel(rubric, level) : "未定级"}
            </Badge>
          )}
          {status === "confirmed" && claim && !level ? (
            <Badge variant="outline">未定级</Badge>
          ) : (
            <StatusBadge kind="assessment_status" value={status} />
          )}
          {capability.category && (
            <Badge variant="outline">{capability.category}</Badge>
          )}
        </div>
        {(levelDescription || item.level_adjusted) && (
          <div className="space-y-1">
            <h3 className="text-xs font-medium text-muted-foreground">
              {item.level_adjusted ? "等级已调整 · 原等级说明" : "等级说明"}
            </h3>
            {levelDescription && <p>{levelDescription}</p>}
          </div>
        )}
        <section className="space-y-1">
          <h3 className="text-xs font-medium text-muted-foreground">
            当前画像陈述
          </h3>
          <p className="whitespace-pre-wrap rounded-md border border-surface-border bg-muted/35 p-3 leading-relaxed">
            {claim?.statement ?? "暂无记录，未评估不代表不具备该能力。"}
          </p>
        </section>
        {claim && (
          <div className="space-y-2 text-xs text-muted-foreground">
            {!compact && (
              <>
                <div className="flex flex-wrap items-center gap-2">
                  <span>把握度</span>
                  <Tooltip>
                    <TooltipTrigger className="cursor-help rounded focus-visible:outline-ring">
                      <StatusBadge kind="confidence" value={claim.confidence} />
                    </TooltipTrigger>
                    <TooltipContent>参考把握度，并非精确概率</TooltipContent>
                  </Tooltip>
                  {actor && <span>来源 · {sources[actor] ?? actor}</span>}
                </div>
                {claim.explanation?.trim() && (
                  <p className="whitespace-pre-wrap">{claim.explanation}</p>
                )}
              </>
            )}
            {claim.created_at && (
              <p>最近更新时间 · {dateLabel(claim.created_at)}</p>
            )}
          </div>
        )}
        {evidence.length > 0 && (
          <section className="space-y-1">
            <h3 className="text-xs font-medium text-muted-foreground">
              相关证据（{evidence.length}）
            </h3>
            <div className="rounded-md border border-surface-border">
              <EvidenceRows
                items={evidence.map((ref) => ({ ...ref, id: ref.evidence_id }))}
                onSelect={onEvidence}
              />
            </div>
          </section>
        )}
        {onViewVersions && (
          <Button
            type="button"
            size="sm"
            variant="link"
            className="self-start px-0"
            onClick={() => {
              setOpen(false)
              onViewVersions()
            }}
          >
            查看版本历史
          </Button>
        )}
        {open &&
          appeals?.requests.flatMap((request) =>
            (request.items ?? [])
              .filter(
                (change) =>
                  change.capability.key === capability.key &&
                  change.decision === "rejected",
              )
              .map((change) => (
                <div
                  key={change.id}
                  className="space-y-2 border-t border-surface-border pt-3"
                >
                  <p className="text-xs text-muted-foreground">
                    已拒绝的变更 · {dateLabel(request.created_at)}
                  </p>
                  <AppealItem
                    orgId={appeals.orgId}
                    requestId={request.id}
                    item={change}
                  />
                </div>
              )),
          )}
        {onEdit && !item.retired && (
          <div className="sticky -bottom-4 -mx-4 mt-auto -mb-4 flex flex-wrap justify-end gap-2 border-t border-surface-border bg-surface px-4 py-3">
            {claim && !directEdit && (
              <Button
                size="sm"
                variant="outline"
                onClick={() => {
                  onEdit(item, true)
                  setOpen(false)
                }}
              >
                移除此项
              </Button>
            )}
            <Button
              size="sm"
              onClick={() => {
                onEdit(item)
                setOpen(false)
              }}
            >
              {directEdit ? "直接修改" : claim ? "修改" : "填写"}
            </Button>
          </div>
        )}
      </div>
    </SideDrawer>
  )
}
