import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import { isAxiosError } from "axios"
import { useState } from "react"
import { type DecisionInput, ReviewsService } from "@/client"
import { EvidenceSheet } from "@/components/Profile/EvidenceSheet"
import { levelLabel } from "@/components/Profile/fields"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { decisionLabels } from "@/components/Profile/RequestTimeline"
import { StatusBadge } from "@/components/Profile/StatusBadge"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { useOrg } from "@/hooks/useOrg"
import { useReviews } from "@/hooks/useReviews"

export const Route = createFileRoute("/_layout/reviews/$requestId")({
  component: ReviewPage,
  head: () => ({ meta: [{ title: "审批请求 - 员工画像平台" }] }),
})
function ReviewPage() {
  const { org } = useOrg()
  const { requestId } = Route.useParams()
  return org ? (
    <ReviewForm
      key={`${org.id}:${requestId}`}
      orgId={org.id}
      requestId={requestId}
    />
  ) : null
}
function ReviewForm({
  orgId,
  requestId,
}: {
  orgId: string
  requestId: string
}) {
  const path = { org_id: orgId, request_id: requestId }
  const cache = useQueryClient()
  const reviews = useReviews()
  const subject = reviews.data?.data.find((request) => request.id === requestId)
  const [decisions, setDecisions] = useState<Record<string, DecisionInput>>({})
  const [comment, setComment] = useState("")
  const [confirm, setConfirm] = useState(false)
  const [revision, setRevision] = useState(0)
  const [stale, setStale] = useState(false)
  const [evidenceId, setEvidenceId] = useState<string | null>(null)
  const query = useQuery({
    queryKey: ["review", orgId, requestId],
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    queryFn: async () => (await ReviewsService.getReview({ path })).data,
    retry: false,
  })
  const mutation = useMutation({
    mutationFn: () =>
      ReviewsService.decide({
        path,
        body: {
          revision,
          decisions: Object.values(decisions),
          comment: comment.trim() || null,
        },
      }),
    onSuccess: () => {
      setConfirm(false)
      cache.invalidateQueries()
    },
    onError: (error) => {
      setConfirm(false)
      if (isAxiosError(error) && error.response?.status === 409) {
        setStale(true)
        setDecisions({})
        query.refetch()
      }
    },
  })
  if (mutation.isSuccess)
    return (
      <div role="status" className="space-y-4">
        <h1 className="text-2xl font-semibold">审批已提交</h1>
        <p>员工可以查看逐项结果和原因。</p>
        <Link className="text-primary underline" to="/reviews">
          返回待审批
        </Link>
      </div>
    )
  if (query.isPending) return <ProfileLoading />
  if (query.isError) {
    if (
      isAxiosError(query.error) &&
      [403, 404].includes(query.error.response?.status ?? 0)
    )
      return (
        <p role="alert">
          {stale
            ? "内容已变化，请重新查看"
            : "404 · 未找到请求或你没有审批权限"}
        </p>
      )
    return <ProfileError retry={() => query.refetch()} />
  }
  const items = query.data.items.filter((item) => !item.after.decision)
  const valid =
    items.length > 0 &&
    items.every((item) => {
      const value = decisions[item.after.id]
      return (
        value &&
        (value.decision === "approved"
          ? item.approvable
          : !!value.reason?.trim())
      )
    })
  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <header className="space-y-2 border-b pb-6">
        <Link className="text-primary underline" to="/reviews">
          返回待审批
        </Link>
        <h1 className="text-2xl font-semibold">逐项审批</h1>
        {subject && (
          <p>
            {subject.employee_name} · {subject.department_name ?? "未分配部门"}
          </p>
        )}
        <p className="text-muted-foreground">
          依据事实核对改动。意见会存为经理评价，员工本人能看到。
        </p>
      </header>
      {!query.data.owner_reviewed && (
        <p className="text-amber-700">员工未过目</p>
      )}
      {stale && <p role="alert">内容已变化，请重新查看</p>}
      {!stale && <SetupError error={mutation.error} />}
      <form
        className="space-y-6"
        onSubmit={(event) => {
          event.preventDefault()
          setRevision(query.data.revision)
          setConfirm(true)
        }}
      >
        {items.map(
          (
            {
              before,
              after,
              evidence,
              jira_only,
              approvable,
              approval_blocked_reason,
            },
            index,
          ) => (
            <section
              key={after.id}
              aria-label={`审批：${after.capability.name}`}
              className="rounded-xl border p-4 sm:p-6 space-y-4"
            >
              <h2 className="text-lg font-semibold">
                {index + 1}. {after.capability.name}
              </h2>
              {jira_only && (
                <p className="rounded-md bg-amber-50 p-3 text-sm text-amber-900">
                  只有 Jira 来源，批准后仍为暂定
                </p>
              )}
              <div className="grid gap-4 sm:grid-cols-2">
                {[
                  { title: "改动前", claim: before },
                  { title: "改动后", claim: after.after },
                ].map(({ title, claim }) => (
                  <div
                    key={title}
                    className="min-w-0 rounded-lg bg-muted/40 p-4 space-y-2"
                  >
                    <h3 className="text-sm text-muted-foreground">{title}</h3>
                    <p className="whitespace-pre-wrap break-words">
                      {claim?.statement ??
                        (title === "改动前"
                          ? "尚无判断"
                          : after.op === "retract"
                            ? "撤回判断"
                            : "保留原判断，补充证据")}
                    </p>
                    {claim && (
                      <>
                        <p className="text-sm">
                          等级：
                          {levelLabel(
                            after.capability.level_rubric,
                            claim.level ?? "未给等级",
                          )}
                        </p>
                        <StatusBadge
                          kind="assessment_status"
                          value={claim.assessment_status}
                        />
                      </>
                    )}
                  </div>
                ))}
              </div>
              <p className="text-sm">
                系统解释：{after.explanation ?? "暂无说明"}
              </p>
              <p className="text-sm">
                不确定之处：{after.uncertainty ?? "暂无说明"}
              </p>
              <p className="text-sm">
                员工依据：{after.owner_statement ?? "暂无说明"}
              </p>
              <p className="text-sm">员工异议：{after.objection ?? "无"}</p>
              <div className="flex flex-wrap gap-2">
                {evidence.map((value) => (
                  <Button
                    type="button"
                    variant="outline"
                    className="h-auto whitespace-normal text-left"
                    key={value.id}
                    onClick={() => setEvidenceId(value.id)}
                  >
                    {value.title}
                  </Button>
                ))}
              </div>
              {!approvable && (
                <p role="status" className="text-destructive">
                  不能批准：{approval_blocked_reason}
                </p>
              )}
              <label className="block space-y-2">
                <span>决定</span>
                <select
                  aria-label={`${after.capability.name}的决定`}
                  required
                  className="w-full rounded-md border bg-background p-2"
                  value={decisions[after.id]?.decision ?? ""}
                  onChange={(event) => {
                    const decision = event.target
                      .value as DecisionInput["decision"]
                    setDecisions({
                      ...decisions,
                      [after.id]: {
                        item_id: after.id,
                        decision,
                        reason: decisions[after.id]?.reason ?? "",
                      },
                    })
                  }}
                >
                  <option value="" disabled>
                    请选择决定
                  </option>
                  {Object.entries(decisionLabels).map(([value, label]) => (
                    <option
                      key={value}
                      value={value}
                      disabled={value === "approved" && !approvable}
                    >
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="block space-y-2">
                <span>原因（退回和拒绝必填）</span>
                <textarea
                  aria-label={`${after.capability.name}的原因`}
                  className="min-h-20 w-full rounded-md border bg-background p-3"
                  disabled={!decisions[after.id]}
                  required={
                    !!decisions[after.id] &&
                    decisions[after.id].decision !== "approved"
                  }
                  value={decisions[after.id]?.reason ?? ""}
                  onChange={(event) =>
                    setDecisions({
                      ...decisions,
                      [after.id]: {
                        ...decisions[after.id],
                        reason: event.target.value,
                      },
                    })
                  }
                />
              </label>
            </section>
          ),
        )}
        <label className="block space-y-2">
          <span>整体意见（选填）</span>
          <textarea
            className="min-h-24 w-full rounded-md border bg-background p-3"
            value={comment}
            onChange={(event) => setComment(event.target.value)}
          />
        </label>
        <Button
          disabled={!valid || mutation.isPending || query.isFetching}
          type="submit"
        >
          核对并提交
        </Button>
      </form>
      <Dialog
        open={confirm}
        onOpenChange={(open) => {
          if (!mutation.isPending) setConfirm(open)
        }}
      >
        <DialogContent className="max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>确认逐项决定</DialogTitle>
            <DialogDescription>
              提交后批准的项立即发布，退回的项生成新草稿。
            </DialogDescription>
          </DialogHeader>
          <ul className="space-y-3">
            {items.map((item) => (
              <li key={item.after.id} className="break-words">
                {item.after.capability.name}：
                {decisions[item.after.id] &&
                  decisionLabels[decisions[item.after.id].decision]}
                <p>{decisions[item.after.id]?.reason || "未填写原因"}</p>
              </li>
            ))}
          </ul>
          {comment.trim() && (
            <p className="whitespace-pre-wrap break-words">
              整体意见：{comment}
            </p>
          )}
          <Button
            disabled={mutation.isPending || !valid || query.isFetching}
            onClick={() => mutation.mutate()}
          >
            {mutation.isPending ? "正在提交…" : "确认提交决定"}
          </Button>
        </DialogContent>
      </Dialog>
      <EvidenceSheet
        orgId={orgId}
        evidenceId={evidenceId}
        onClose={() => setEvidenceId(null)}
      />
    </div>
  )
}
