import { useMutation, useQueryClient } from "@tanstack/react-query"
import { ChevronDown } from "lucide-react"
import { type ReactNode, useId, useState } from "react"
import {
  type ChangeItemPublic,
  type ChangeRequestPublic,
  EmployeeRequestsService,
  type ProfileItem,
  type RequestStatus,
} from "@/client"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import { AppealItem } from "./AppealItem"
import type { EditorSelection } from "./ChangeRequestEditor"
import { EvidenceRows } from "./EvidenceList"
import { levelLabel, record } from "./fields"
import { RequestReviewers } from "./RequestReviewers"
import { decisionLabels, RequestTimeline } from "./RequestTimeline"
import { StatusBadge } from "./StatusBadge"

const statusLabels: Record<RequestStatus, string> = {
  draft: "草稿",
  pending_owner: "待本人确认",
  pending_review: "待经理审批",
  approved: "已批准",
  changes_requested: "已退回",
  rejected: "已拒绝",
  withdrawn: "已撤回",
}

function requestSummary(request: ChangeRequestPublic) {
  const items = request.items ?? []
  const labels = {
    approved: "批准",
    changes_requested: "退回",
    rejected: "拒绝",
    pending: "待决定",
  }
  const counts = Object.entries(labels)
    .map(([decision, label]) => ({
      label,
      count: items.filter((item) => (item.decision ?? "pending") === decision)
        .length,
    }))
    .filter(({ count }) => count > 0)
  return counts.length > 1
    ? counts.map(({ label, count }) => `${count} 项${label}`).join("、")
    : `${statusLabels[request.status]} · ${items.length} 项改动`
}

export function ChangeRequestCard({
  requests,
  employeeId,
  isOwner,
  onEvidence,
  orgId,
  onEdit,
  compact = false,
  profileItems = [],
}: {
  compact?: boolean
  profileItems?: ProfileItem[]
  requests: ChangeRequestPublic[]
  employeeId: string
  isOwner: boolean
  onEvidence: (id: string) => void
  orgId: string
  onEdit: (selection: EditorSelection) => void
}) {
  const client = useQueryClient()
  const withdraw = useMutation({
    mutationFn: (id: string) =>
      EmployeeRequestsService.withdrawRequest({
        path: { org_id: orgId, request_id: id },
      }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["change-requests", orgId] })
      client.invalidateQueries({ queryKey: ["profile", orgId] })
      client.invalidateQueries({ queryKey: ["inbox", orgId] })
    },
  })
  const pending = requests.filter(
    (request) =>
      ["initial_sync", "import"].includes(request.origin) &&
      request.status === "pending_owner",
  )
  const others = requests.filter((request) => !pending.includes(request))
  const open = others.filter((request) =>
    ["draft", "pending_owner", "pending_review"].includes(request.status),
  )
  const returnedCount = others.filter(
    (request) => request.status === "changes_requested",
  ).length
  const actionable = others.filter(
    (request) =>
      open.includes(request) || request.status === "changes_requested",
  )
  const groups = [
    { title: "其他待处理请求", requests: actionable, returnedCount },
    {
      title: "已处理请求",
      requests: others.filter((request) => !actionable.includes(request)),
    },
  ]
  return (
    <div className="empty:hidden space-y-2">
      <SetupError error={withdraw.error} />
      {pending.map((initial) => (
        <RequestDisclosure
          key={initial.id}
          id={`request-${initial.id}`}
          label={initial.origin === "import" ? "导入画像草稿" : "初始画像草稿"}
          summary={`${isOwner ? "待你确认：" : "待本人确认："}${initial.origin === "import" ? "管理员导入的画像" : "初始画像草稿"} · ${initial.items?.length ?? 0} 项改动`}
          pending
        >
          <div className="space-y-2">
            <h2 className="sr-only">
              {isOwner ? "待你确认：" : "待本人确认："}
              {initial.origin === "import"
                ? "管理员导入的画像"
                : "初始画像草稿"}
            </h2>
            <p className="text-sm leading-relaxed text-muted-foreground">
              {initial.origin === "import"
                ? "由管理员导入，尚未生效。"
                : "根据工作记录初步整理，尚未生效。"}
              {isOwner ? "你" : "本人"}可以直接提交、修改或提出异议。
            </p>
          </div>
          {!initial.items?.length && (
            <div className="space-y-3">
              <p>暂未匹配到对应的工作记录，你可以手动补充。</p>
              {isOwner && (
                <Button
                  variant="outline"
                  onClick={() => onEdit({ request: initial })}
                >
                  补充初始画像
                </Button>
              )}
            </div>
          )}
          <div className="grid gap-3 lg:grid-cols-2">
            {(initial.items ?? []).map((item) => (
              <section
                key={item.id}
                aria-label={`草稿改动：${item.capability.name}`}
                className="space-y-2 rounded-lg border border-surface-border bg-surface p-3"
              >
                {compact ? (
                  <CompactChange item={item} profileItems={profileItems} />
                ) : (
                  <>
                    <div className="flex items-center justify-between gap-3">
                      <h3 className="font-semibold">{item.capability.name}</h3>
                      {item.after && (
                        <StatusBadge
                          kind="assessment_status"
                          value={item.after.assessment_status}
                        />
                      )}
                    </div>
                    <p>{item.after?.statement}</p>
                    <p className="text-sm">
                      等级：
                      {item.after?.level
                        ? levelLabel(
                            item.capability.level_rubric,
                            item.after.level,
                          )
                        : "未定级"}
                    </p>
                    <p className="text-sm text-muted-foreground">
                      补充说明：
                      {item.owner_statement ?? item.explanation ?? "暂无说明"}
                    </p>
                    <p className="text-sm text-muted-foreground">
                      待核实点：{item.uncertainty ?? "暂无说明"}
                    </p>
                    <EvidenceRows
                      items={(item.evidence ?? []).map((evidence) => ({
                        ...evidence,
                        id: evidence.evidence_id,
                      }))}
                      onSelect={onEvidence}
                    />
                  </>
                )}
                {isOwner && (
                  <div className="flex flex-wrap gap-2">
                    <Button
                      variant="outline"
                      onClick={() =>
                        onEdit({
                          request: initial,
                          capabilityKey: item.capability.key,
                        })
                      }
                    >
                      修改
                    </Button>
                    <Button
                      variant="ghost"
                      onClick={() =>
                        onEdit({
                          request: initial,
                          capabilityKey: item.capability.key,
                          objection: true,
                        })
                      }
                    >
                      提异议
                    </Button>
                  </div>
                )}
              </section>
            ))}
          </div>
          {!compact && (
            <RequestTimeline
              orgId={orgId}
              employeeId={employeeId}
              requestId={initial.id}
            />
          )}
          {isOwner && (
            <Button
              disabled={!initial.items?.length}
              onClick={() => onEdit({ request: initial, preview: true })}
            >
              直接提交
            </Button>
          )}
        </RequestDisclosure>
      ))}
      {groups.map(
        ({ title, requests: group, returnedCount }) =>
          group.length > 0 && (
            <RequestDisclosure
              key={title}
              label={title}
              summary={`${title} · ${group.length} 条${returnedCount ? ` · 已退回 ${returnedCount} 条` : ""}`}
              pending={title === "其他待处理请求"}
            >
              <ul className="space-y-2 text-sm text-muted-foreground">
                {group.map((request) => (
                  <li
                    key={request.id}
                    id={`request-${request.id}`}
                    className="space-y-3 border-b py-3 last:border-0"
                  >
                    {request.origin === "assignment_feedback" && "工作反馈 · "}
                    {requestSummary(request)}
                    {isOwner && request.status === "pending_review" && (
                      <RequestReviewers reviewers={request.reviewers} />
                    )}
                    {!compact && request.submitted_via === "admin_handover" && (
                      <span> · 员工未确认</span>
                    )}
                    {open.includes(request) &&
                      request.items?.map((item) => (
                        <CompactChange
                          key={item.id}
                          item={item}
                          profileItems={profileItems}
                        />
                      ))}
                    {request.origin === "assignment_feedback" &&
                      open.includes(request) && (
                        <EvidenceRows
                          items={Array.from(
                            new Map(
                              request.items?.flatMap((item) =>
                                (item.evidence ?? []).map(
                                  (ref) =>
                                    [
                                      ref.evidence_id,
                                      { ...ref, id: ref.evidence_id },
                                    ] as const,
                                ),
                              ),
                            ).values(),
                          )}
                          onSelect={onEvidence}
                        />
                      )}
                    {request.items?.some((item) => item.decision) && (
                      <ul className="space-y-2">
                        {request.items.map((item) => (
                          <li key={item.id}>
                            {item.capability.name}：
                            {item.decision
                              ? decisionLabels[item.decision]
                              : "待决定"}
                            {!compact && item.decision_reason && (
                              <p className="whitespace-pre-wrap break-words">
                                原因：{item.decision_reason}
                              </p>
                            )}
                            {isOwner && item.decision === "rejected" && (
                              <AppealItem
                                orgId={orgId}
                                requestId={request.id}
                                item={item}
                              />
                            )}
                          </li>
                        ))}
                      </ul>
                    )}
                    {!compact && (
                      <RequestTimeline
                        orgId={orgId}
                        employeeId={employeeId}
                        requestId={request.id}
                      />
                    )}
                    {isOwner &&
                      (request.status === "draft" ||
                        (request.origin === "assignment_feedback" &&
                          request.status === "pending_owner")) && (
                        <Button
                          className="ml-2"
                          variant="outline"
                          onClick={() => onEdit({ request })}
                        >
                          {request.status === "pending_owner"
                            ? "确认并提交"
                            : "继续修改"}
                        </Button>
                      )}
                    {isOwner && request.status === "pending_review" && (
                      <Button
                        className="ml-2"
                        variant="outline"
                        disabled={withdraw.isPending}
                        onClick={() => {
                          if (
                            window.confirm(
                              "撤回后本次申请将取消，已发布的内容不受影响。确认撤回吗？",
                            )
                          )
                            withdraw.mutate(request.id)
                        }}
                      >
                        撤回
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
            </RequestDisclosure>
          ),
      )}
    </div>
  )
}

function RequestDisclosure({
  id,
  label,
  summary,
  pending,
  children,
}: {
  id?: string
  label: string
  summary: string
  pending?: boolean
  children: ReactNode
}) {
  return (
    <details
      className={`group/requests rounded-lg border bg-surface ${pending ? "border-amber-500/40" : "border-surface-border"}`}
    >
      <summary
        className={`flex cursor-pointer list-none items-center justify-between gap-2 rounded-lg px-3 py-2 text-sm font-medium group-open/requests:rounded-b-none [&::-webkit-details-marker]:hidden ${pending ? "bg-amber-500/10" : "bg-muted/40"}`}
      >
        {summary}
        <ChevronDown
          aria-hidden="true"
          className="size-4 shrink-0 transition-transform group-open/requests:rotate-180"
        />
      </summary>
      <section
        id={id}
        aria-label={label}
        className="space-y-3 border-t border-surface-border p-3"
      >
        {children}
      </section>
    </details>
  )
}

function CompactChange({
  item,
  profileItems,
}: {
  item: ChangeItemPublic
  profileItems: ProfileItem[]
}) {
  const [expanded, setExpanded] = useState(false)
  const statementId = useId()
  const before = profileItems.find(
    (entry) => entry.claim?.id === item.target_claim_id,
  )?.claim
  const displayLevel = (level: string | null | undefined) => {
    if (!level) return "未定级"
    const rubric = item.capability.level_rubric
    if (
      !record(rubric) ||
      !Array.isArray(rubric.levels) ||
      !rubric.levels.some(
        (entry: unknown) => record(entry) && entry.key === level,
      )
    )
      return "等级已调整"
    return levelLabel(rubric, level)
  }
  return (
    <div className="space-y-2">
      <h3 className="font-semibold">{item.capability.name}</h3>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span>
          修改前：
          {before?.level
            ? displayLevel(before.level)
            : before
              ? "未定级"
              : item.target_claim_id
                ? "原记录"
                : "暂无记录"}
        </span>
        {(before || !item.target_claim_id) && (
          <StatusBadge
            kind="assessment_status"
            value={before?.assessment_status ?? "unknown"}
          />
        )}
        {item.op === "attach_evidence" ? (
          <span>补充依据</span>
        ) : item.after ? (
          <>
            <span>→ 修改后：{displayLevel(item.after.level)}</span>
            <StatusBadge
              kind="assessment_status"
              value={item.after.assessment_status}
            />
          </>
        ) : (
          <span>撤回</span>
        )}
      </div>
      <p
        id={statementId}
        className={`whitespace-pre-wrap break-words ${expanded ? "" : "line-clamp-2"}`}
      >
        {item.after?.statement ??
          before?.statement ??
          (item.op === "retract" ? "移除此项" : "仅补充证据")}
      </p>
      {(item.after?.statement || before?.statement) && (
        <Button
          variant="link"
          size="sm"
          aria-controls={statementId}
          aria-expanded={expanded}
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? "收起" : "展开全文"}
        </Button>
      )}
    </div>
  )
}
