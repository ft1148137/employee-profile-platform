import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import { isAxiosError } from "axios"
import { ArrowLeft, ArrowRight, FileText } from "lucide-react"
import { useState } from "react"
import {
  type DecisionInput,
  type PendingReviews,
  ReviewsService,
} from "@/client"
import { TopbarActions } from "@/components/Common/TopbarSlot"
import {
  SectionCard,
  WorkspacePage,
} from "@/components/Common/WorkbenchPrimitives"
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
import { cn } from "@/lib/utils"

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
const decisionOptions = Object.entries(decisionLabels) as [
  DecisionInput["decision"],
  string,
][]

const decisionStyles = {
  approved:
    "aria-pressed:bg-emerald-600 aria-pressed:text-white aria-pressed:hover:bg-emerald-600",
  changes_requested:
    "aria-pressed:bg-amber-400 aria-pressed:text-amber-950 aria-pressed:hover:bg-amber-400",
  rejected:
    "aria-pressed:bg-destructive aria-pressed:text-white aria-pressed:hover:bg-destructive",
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
    // Review access can expire; discard restricted content when its view closes.
    gcTime: 0,
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
    onSuccess: async () => {
      const queryKey = ["reviews", orgId]
      await cache.cancelQueries({ queryKey })
      cache.setQueryData<PendingReviews>(queryKey, (previous) => {
        if (!previous) return previous
        const data = previous.data.filter((request) => request.id !== requestId)
        return { ...previous, data, count: data.length }
      })
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
        <p>审批结果已通知员工。</p>
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
        <div role="alert" className="space-y-4">
          <p>
            {stale
              ? "内容已更新，请刷新查看"
              : "该申请已处理、已撤回，或你没有审批权限"}
          </p>
          <Link className="text-primary underline" to="/reviews">
            返回待审批
          </Link>
        </div>
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
    <WorkspacePage>
      <h1 className="sr-only">画像审批</h1>
      <TopbarActions>
        <Button asChild size="sm" variant="outline">
          <Link to="/reviews">
            <ArrowLeft aria-hidden="true" />
            返回待审批
          </Link>
        </Button>
      </TopbarActions>
      {stale && (
        <p role="alert" className="mb-3 text-sm text-destructive">
          内容已更新，请刷新查看
        </p>
      )}
      {!stale && <SetupError error={mutation.error} />}
      <form
        className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(280px,31%)]"
        onSubmit={(event) => {
          event.preventDefault()
          if (!valid || mutation.isPending || query.isFetching) return
          setRevision(query.data.revision)
          setConfirm(true)
        }}
      >
        <section aria-label="待审批变更项" className="min-w-0 space-y-3">
          <h2 className="text-sm font-semibold">
            待审批变更项{" "}
            <span className="text-muted-foreground">（{items.length}）</span>
          </h2>
          {items.map(({ before, after, evidence, jira_only }) => (
            <SectionCard key={after.id} className="space-y-3">
              <h3 className="wrap-anywhere text-sm font-semibold">
                {after.capability.name}
              </h3>
              <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)]">
                {[
                  { title: "改动前", claim: before, fallback: "暂无记录" },
                  {
                    title: "改动后",
                    claim: after.after,
                    fallback:
                      after.op === "retract" ? "移除此项" : "仅补充证据",
                  },
                ].map(({ title, claim, fallback }, index) => (
                  <div
                    key={title}
                    className={cn(
                      "min-w-0 rounded-md bg-muted/60 p-2.5 text-sm",
                      index === 1 && "sm:col-start-3 sm:row-start-1",
                    )}
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <h4 className="text-xs text-muted-foreground">{title}</h4>
                      {claim && (
                        <>
                          <span className="font-medium">
                            {levelLabel(
                              after.capability.level_rubric,
                              claim.level ?? "未定级",
                            )}
                          </span>
                          <StatusBadge
                            kind="assessment_status"
                            value={claim.assessment_status}
                          />
                        </>
                      )}
                    </div>
                    <p className="mt-1 whitespace-pre-wrap wrap-anywhere">
                      {claim?.statement ?? fallback}
                    </p>
                  </div>
                ))}
                <ArrowRight
                  aria-hidden="true"
                  className="hidden size-4 self-center text-primary sm:col-start-2 sm:row-start-1 sm:block"
                />
              </div>
              {jira_only && (
                <p className="rounded bg-amber-500/10 px-2.5 py-1.5 text-xs text-amber-800 dark:text-amber-300">
                  仅来自 Jira，批准后为「暂定」
                </p>
              )}
              {[
                {
                  label: "起草说明",
                  text: after.explanation,
                  style: "border-surface-border bg-muted/60",
                },
                {
                  label: "待核实点",
                  text: after.uncertainty,
                  style: "border-amber-500 bg-amber-500/10",
                },
                {
                  label: "员工补充",
                  text: after.owner_statement,
                  style: "border-primary/50 bg-primary/5",
                },
                {
                  label: "员工异议",
                  text: after.objection,
                  style: "border-destructive bg-destructive/5",
                },
              ].map(
                ({ label, text, style }) =>
                  text?.trim() && (
                    <p
                      key={label}
                      className={cn(
                        "whitespace-pre-wrap wrap-anywhere border-l-2 px-2.5 py-1.5 text-sm",
                        style,
                      )}
                    >
                      <span className="font-medium">{label}：</span>
                      {text}
                    </p>
                  ),
              )}
              {evidence.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {evidence.map(({ id, title }) => (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="h-auto max-w-full rounded-full py-1 whitespace-normal wrap-anywhere text-left text-xs shadow-none"
                      key={id}
                      onClick={() => setEvidenceId(id)}
                    >
                      <FileText aria-hidden="true" />
                      {title}
                    </Button>
                  ))}
                </div>
              )}
            </SectionCard>
          ))}
        </section>
        <SectionCard
          aria-label="审批决策面板"
          className="flex min-w-0 flex-col p-0 lg:sticky lg:top-16 lg:max-h-[calc(100dvh-5rem)]"
        >
          <header className="flex flex-wrap items-center justify-between gap-1 border-surface-border border-b px-4 py-3">
            <h2 className="text-sm font-semibold">审批决策面板</h2>
            <span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs text-primary">
              {Object.keys(decisions).length}/{items.length} 已选
            </span>
          </header>
          <div className="min-h-0 divide-y divide-surface-border px-4 lg:overflow-y-auto">
            {(subject ||
              query.data.acting_for_name ||
              query.data.assignment_reason ||
              !query.data.owner_reviewed) && (
              <div className="space-y-1 py-3 text-sm wrap-anywhere">
                {subject && (
                  <p className="font-medium">
                    {subject.employee_name} ·{" "}
                    {subject.department_name ?? "未分配部门"}
                  </p>
                )}
                {query.data.acting_for_name && (
                  <p className="font-medium text-primary">
                    代{query.data.acting_for_name}审批
                  </p>
                )}
                {query.data.assignment_reason && (
                  <p className="text-muted-foreground">
                    分配原因：{query.data.assignment_reason}
                  </p>
                )}
                {!query.data.owner_reviewed && (
                  <p className="text-amber-700 dark:text-amber-300">
                    员工未确认
                  </p>
                )}
              </div>
            )}
            {items.map(({ after, approvable, approval_blocked_reason }) => {
              const value = decisions[after.id]
              return (
                <fieldset key={after.id} className="min-w-0 space-y-2 py-3">
                  <legend className="sr-only">
                    {after.capability.name}的审批意见
                  </legend>
                  <h3 className="wrap-anywhere text-sm font-semibold">
                    {after.capability.name}
                  </h3>
                  <div className="grid grid-cols-3 gap-0.5 rounded-md bg-muted p-0.5">
                    {decisionOptions.map(([decision, label]) => (
                      <Button
                        key={decision}
                        type="button"
                        variant="ghost"
                        size="sm"
                        className={cn("px-1", decisionStyles[decision])}
                        aria-pressed={value?.decision === decision}
                        disabled={decision === "approved" && !approvable}
                        onClick={() =>
                          setDecisions((previous) => ({
                            ...previous,
                            [after.id]: {
                              item_id: after.id,
                              decision,
                              reason:
                                decision === "approved"
                                  ? ""
                                  : (previous[after.id]?.reason ?? ""),
                            },
                          }))
                        }
                      >
                        {label}
                      </Button>
                    ))}
                  </div>
                  {!approvable && (
                    <p role="status" className="text-xs text-destructive">
                      不能批准：{approval_blocked_reason}
                    </p>
                  )}
                  {value && value.decision !== "approved" && (
                    <label className="block space-y-1 text-xs text-muted-foreground">
                      <span>{decisionLabels[value.decision]}原因（必填）</span>
                      <textarea
                        aria-label={`${after.capability.name}的原因`}
                        className="min-h-16 w-full rounded-md border bg-background p-2 text-sm text-foreground"
                        required
                        value={value.reason ?? ""}
                        onChange={(event) =>
                          setDecisions({
                            ...decisions,
                            [after.id]: {
                              ...value,
                              reason: event.target.value,
                            },
                          })
                        }
                      />
                    </label>
                  )}
                </fieldset>
              )
            })}
            <label className="block space-y-1 py-3 text-xs text-muted-foreground">
              <span>整体意见（选填）</span>
              <textarea
                className="min-h-16 w-full rounded-md border bg-background p-2 text-sm text-foreground"
                value={comment}
                onChange={(event) => setComment(event.target.value)}
              />
            </label>
          </div>
          <footer className="sticky bottom-0 space-y-2 rounded-b-lg border-surface-border border-t bg-surface px-4 py-3">
            <Button
              className="w-full"
              disabled={!valid || mutation.isPending || query.isFetching}
              type="submit"
            >
              提交审批
            </Button>
            <p className="text-center text-xs text-muted-foreground">
              审批意见员工本人可见。
            </p>
          </footer>
        </SectionCard>
      </form>
      <Dialog
        open={confirm}
        onOpenChange={(open) => {
          if (!mutation.isPending) setConfirm(open)
        }}
      >
        <DialogContent className="max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>确认提交审批</DialogTitle>
            <DialogDescription>
              批准项将立即生效，退回项将返回给员工修改。
            </DialogDescription>
          </DialogHeader>
          <ul className="space-y-3">
            {items.map((item) => (
              <li key={item.after.id} className="break-words">
                {item.after.capability.name}：
                {decisions[item.after.id] &&
                  decisionLabels[decisions[item.after.id].decision]}
                {decisions[item.after.id]?.reason?.trim() && (
                  <p>{decisions[item.after.id].reason}</p>
                )}
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
            {mutation.isPending ? "正在提交…" : "确认提交"}
          </Button>
        </DialogContent>
      </Dialog>
      <EvidenceSheet
        orgId={orgId}
        evidenceId={evidenceId}
        onClose={() => setEvidenceId(null)}
      />
    </WorkspacePage>
  )
}
