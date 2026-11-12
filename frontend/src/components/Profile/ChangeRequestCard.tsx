import { useMutation, useQueryClient } from "@tanstack/react-query"
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
import { levelLabel } from "./fields"
import { decisionLabels, RequestTimeline } from "./RequestTimeline"
import { StatusBadge } from "./StatusBadge"

const statusLabels: Record<RequestStatus, string> = {
  draft: "草稿",
  pending_owner: "待本人过目",
  pending_review: "待经理审批",
  approved: "已批准",
  changes_requested: "已退回",
  rejected: "已拒绝",
  withdrawn: "已撤回",
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
  const groups = [
    { title: "其他待处理请求", requests: open },
    {
      title: "已处理请求",
      requests: others.filter((request) => !open.includes(request)),
    },
  ]
  return (
    <div className="mb-6 space-y-4">
      <SetupError error={withdraw.error} />
      {pending.map((initial) => (
        <section
          key={initial.id}
          id={`request-${initial.id}`}
          aria-label={
            initial.origin === "import" ? "导入画像草稿" : "初始画像草稿"
          }
          className="rounded-xl border border-primary/30 bg-muted/20 p-6 space-y-5"
        >
          <div className="space-y-2">
            <h2 className="text-lg font-semibold">
              {isOwner ? "待你过目：" : "待本人过目："}
              {initial.origin === "import"
                ? "管理员导入的画像"
                : "系统起草的初始画像"}
            </h2>
            <p className="text-sm leading-relaxed text-muted-foreground">
              {initial.origin === "import"
                ? "这是管理员导入的画像草稿"
                : "这是系统根据工作记录整理的草稿"}
              ，还没有生效。本人可以直接提交、修改后提交或提出异议。
            </p>
          </div>
          {!initial.items?.length && (
            <div className="space-y-3">
              <p>系统没有找到能对应到能力词典的工作记录。</p>
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
          <div className="grid gap-4 xl:grid-cols-2">
            {(initial.items ?? []).map((item) => (
              <section
                key={item.id}
                aria-label={`草稿改动：${item.capability.name}`}
                className="rounded-lg border bg-card p-5 space-y-3"
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
                        : "未给等级"}
                    </p>
                    <p className="text-sm text-muted-foreground">
                      依据说明：
                      {item.owner_statement ?? item.explanation ?? "暂无说明"}
                    </p>
                    <p className="text-sm text-muted-foreground">
                      不确定之处：{item.uncertainty ?? "暂无说明"}
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
        </section>
      ))}
      {groups.map(
        ({ title, requests: group }) =>
          group.length > 0 && (
            <section
              key={title}
              aria-label={title}
              className="rounded-lg border p-4"
            >
              <h2 className="mb-2 font-semibold">{title}</h2>
              <ul className="space-y-2 text-sm text-muted-foreground">
                {group.map((request) => (
                  <li
                    key={request.id}
                    id={`request-${request.id}`}
                    className="space-y-3 border-b py-3 last:border-0"
                  >
                    {statusLabels[request.status]} ·{" "}
                    {request.items?.length ?? 0} 项改动
                    {!compact && request.submitted_via === "admin_handover" && (
                      <span> · 员工未过目</span>
                    )}
                    {compact &&
                      open.includes(request) &&
                      request.items?.map((item) => (
                        <CompactChange
                          key={item.id}
                          item={item}
                          profileItems={profileItems}
                        />
                      ))}
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
                    {isOwner && request.status === "draft" && (
                      <Button
                        className="ml-2"
                        variant="outline"
                        onClick={() => onEdit({ request })}
                      >
                        继续修改
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
                              "确定撤回待审批的改动？已发布的改动仍然保留。",
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
            </section>
          ),
      )}
    </div>
  )
}

function CompactChange({
  item,
  profileItems,
}: {
  item: ChangeItemPublic
  profileItems: ProfileItem[]
}) {
  const before = profileItems.find(
    (entry) => entry.claim?.id === item.target_claim_id,
  )?.claim
  return (
    <div className="space-y-2">
      <h3 className="font-semibold">{item.capability.name}</h3>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span>
          修改前：
          {before?.level
            ? levelLabel(item.capability.level_rubric, before.level)
            : before
              ? "未给等级"
              : item.target_claim_id
                ? "历史判断"
                : "暂无判断"}
        </span>
        {(before || !item.target_claim_id) && (
          <StatusBadge
            kind="assessment_status"
            value={before?.assessment_status ?? "unknown"}
          />
        )}
        <span>
          → 修改后：
          {item.after?.level
            ? levelLabel(item.capability.level_rubric, item.after.level)
            : "未给等级"}
        </span>
        {item.after ? (
          <StatusBadge
            kind="assessment_status"
            value={item.after.assessment_status}
          />
        ) : (
          <span>撤回</span>
        )}
      </div>
    </div>
  )
}
