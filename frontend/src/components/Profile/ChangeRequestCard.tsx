import { useMutation, useQueryClient } from "@tanstack/react-query"
import {
  type ChangeRequestPublic,
  EmployeeRequestsService,
  type RequestStatus,
} from "@/client"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import type { EditorSelection } from "./ChangeRequestEditor"
import { EvidenceRows } from "./EvidenceList"
import { levelLabel } from "./fields"
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
  isOwner,
  onEvidence,
  orgId,
  onEdit,
}: {
  requests: ChangeRequestPublic[]
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
  return (
    <div className="mb-6 space-y-4">
      <SetupError error={withdraw.error} />
      {pending.map((initial) => (
        <section
          key={initial.id}
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
                    ? levelLabel(item.capability.level_rubric, item.after.level)
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
      {others.length > 0 && (
        <section aria-label="其他待处理请求" className="rounded-lg border p-4">
          <h2 className="mb-2 font-semibold">其他待处理请求</h2>
          <ul className="space-y-2 text-sm text-muted-foreground">
            {others.map((request) => (
              <li key={request.id}>
                {statusLabels[request.status]} · {request.items?.length ?? 0}{" "}
                项改动
                {request.submitted_via === "admin_handover" && (
                  <span> · 员工未过目</span>
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
                          "确定撤回这个请求？经理将不再审批此请求。",
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
      )}
    </div>
  )
}
