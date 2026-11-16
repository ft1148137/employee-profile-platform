import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { CapacityService } from "@/client"
import { dateLabel } from "@/components/Profile/fields"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { WorkloadNumbers, weekLabel, weekLoadLabel } from "./shared"

export function MyWorkload({ orgId }: { orgId: string }) {
  const client = useQueryClient()
  const [percent, setPercent] = useState("")
  const [reason, setReason] = useState("")
  const query = useQuery({
    queryKey: ["capacity", orgId, "me"],
    queryFn: async () =>
      (await CapacityService.getMyWorkload({ path: { org_id: orgId } })).data,
  })
  const submit = useMutation({
    mutationFn: () =>
      CapacityService.requestWorkloadChange({
        path: { org_id: orgId },
        body: { requested_percent: Number(percent), reason: reason.trim() },
      }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["capacity", orgId] })
      setReason("")
    },
  })
  if (query.isPending) return <ProfileLoading />
  if (query.isError) return <ProfileError retry={() => query.refetch()} />
  const pending = query.data.requests.some(
    (request) => request.status === "pending",
  )
  return (
    <div className="space-y-6">
      <WorkloadNumbers workload={query.data} />
      <section
        aria-label="每周投入"
        className="grid grid-cols-2 gap-3 sm:grid-cols-4"
      >
        {query.data.weeks.map((week) => (
          <div key={week.week_start} className="rounded-lg border p-3">
            <p className="text-sm text-muted-foreground">
              {weekLabel(week.week_start)}
            </p>
            <p className="mt-2 font-medium tabular-nums">
              {weekLoadLabel(week)}
            </p>
            {week.legacy_start && (
              <p className="text-xs text-muted-foreground">开始日期未确认</p>
            )}
          </div>
        ))}
      </section>
      <form
        aria-label="申请调整"
        className="max-w-xl space-y-4 rounded-xl border p-5"
        onSubmit={(event) => {
          event.preventDefault()
          submit.mutate()
        }}
      >
        <h2 className="text-lg font-semibold">申请调整</h2>
        <p className="text-sm text-muted-foreground">
          填写平台外其他工作的占比（0–200%），平台内分配将另行计入经理确认值。
        </p>
        <fieldset disabled={pending || submit.isPending} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="requested-percent">申请负荷（%）</Label>
            <Input
              id="requested-percent"
              type="number"
              min={0}
              max={200}
              step={1}
              required
              value={percent}
              onChange={(event) => setPercent(event.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="workload-reason">申请原因</Label>
            <textarea
              className="w-full rounded-md border bg-transparent p-3 text-sm"
              id="workload-reason"
              required
              maxLength={500}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </div>
          <Button type="submit" disabled={!reason.trim()}>
            {submit.isPending ? "正在提交…" : "提交申请"}
          </Button>
        </fieldset>
        {pending && <p role="status">已有申请待处理，请等待经理答复。</p>}
        <SetupError error={submit.error} />
      </form>
      <section aria-label="调整申请记录" className="space-y-3">
        <h2 className="text-lg font-semibold">调整申请记录</h2>
        {!query.data.requests.length && (
          <p className="text-muted-foreground">暂无调整申请。</p>
        )}
        {query.data.requests.map((request) => (
          <article key={request.id} className="space-y-2 rounded-xl border p-4">
            <p className="font-medium">
              {request.requested_percent}% ·{" "}
              {
                { pending: "待处理", approved: "已同意", returned: "已退回" }[
                  request.status
                ]
              }
            </p>
            <p className="whitespace-pre-wrap break-words">{request.reason}</p>
            {request.decision_note && (
              <p className="whitespace-pre-wrap break-words">
                处理说明：{request.decision_note}
              </p>
            )}
            <p className="text-xs text-muted-foreground">
              {dateLabel(request.created_at)}
            </p>
          </article>
        ))}
      </section>
    </div>
  )
}
