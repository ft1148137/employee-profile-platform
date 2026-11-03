import type { ChangeRequestPublic, RequestStatus } from "@/client"
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
}: {
  requests: ChangeRequestPublic[]
  isOwner: boolean
  onEvidence: (id: string) => void
}) {
  const initial = requests.find(
    (request) =>
      request.origin === "initial_sync" && request.status === "pending_owner",
  )
  const others = requests.filter((request) => request !== initial)
  return (
    <div className="mb-6 space-y-4">
      {initial && (
        <section
          aria-label="初始画像草稿"
          className="rounded-xl border border-primary/30 bg-muted/20 p-6 space-y-5"
        >
          <div className="space-y-2">
            <h2 className="text-lg font-semibold">
              {isOwner
                ? "待你过目：系统起草的初始画像"
                : "待本人过目：系统起草的初始画像"}
            </h2>
            <p className="text-sm leading-relaxed text-muted-foreground">
              这是系统根据工作记录整理的草稿，还没有生效。目前只能查看，确认和补充将在后续版本提供。
            </p>
          </div>
          {!initial.items?.length && (
            <p>系统没有找到能对应到能力词典的工作记录。</p>
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
                  解释：{item.explanation ?? "暂无解释"}
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
              </section>
            ))}
          </div>
        </section>
      )}
      {others.length > 0 && (
        <section aria-label="其他待处理请求" className="rounded-lg border p-4">
          <h2 className="mb-2 font-semibold">其他待处理请求</h2>
          <ul className="space-y-2 text-sm text-muted-foreground">
            {others.map((request) => (
              <li key={request.id}>
                {statusLabels[request.status]} · {request.items?.length ?? 0}{" "}
                项改动
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}
