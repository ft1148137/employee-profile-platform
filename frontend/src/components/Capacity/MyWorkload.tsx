import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { CapacityService } from "@/client"
import { dateLabel } from "@/components/Profile/fields"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { MyAssignments } from "./MyAssignments"
import { addWeeks, WorkloadNumbers, weekLabel, weekLoadLabel } from "./shared"

export function MyWorkload({ orgId }: { orgId: string }) {
  const client = useQueryClient()
  const [percent, setPercent] = useState("")
  const [reason, setReason] = useState("")
  const [start, setStart] = useState("")
  const [end, setEnd] = useState("")
  const query = useQuery({
    queryKey: ["capacity", orgId, "me"],
    queryFn: async () =>
      (await CapacityService.getMyWorkload({ path: { org_id: orgId } })).data,
  })
  const submit = useMutation({
    mutationFn: () =>
      CapacityService.requestWorkloadChange({
        path: { org_id: orgId },
        body: {
          requested_percent: Number(percent),
          reason: reason.trim(),
          start_week: start || query.data!.weeks[0].week_start,
          end_week: end || query.data!.weeks[0].week_start,
        },
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
  const reviewerNames = query.data.reviewer_names.join("、")
  const submittedNames = submit.data?.data.reviewer_names?.join("、")
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
            {query.data.correction_pending[week.week_start] && (
              <p className="text-xs">调整审批中</p>
            )}
            {week.capacity_percent < 100 && (
              <p className="text-xs">可用 {week.capacity_percent}%</p>
            )}
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
          填写平台外工作的精力占比（0–200%），平台内已分配任务会自动累加。
        </p>
        <p className="text-sm text-muted-foreground">
          {reviewerNames
            ? `将交给 ${reviewerNames} 审批`
            : "暂无审批人，请联系企业管理员"}
        </p>
        <fieldset disabled={pending || submit.isPending} className="space-y-4">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="request-start-week">开始周</Label>
              <Input
                id="request-start-week"
                type="date"
                min="1970-01-05"
                step={7}
                required
                value={start || query.data.weeks[0].week_start}
                onChange={(event) => setStart(event.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="request-end-week">结束周</Label>
              <Input
                id="request-end-week"
                type="date"
                min={start || query.data.weeks[0].week_start}
                max={addWeeks(start || query.data.weeks[0].week_start, 25)}
                step={7}
                required
                value={end || query.data.weeks[0].week_start}
                onChange={(event) => setEnd(event.target.value)}
              />
            </div>
          </div>
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
        {pending && <p role="status">当前有申请正在审批中，请等待处理。</p>}
        {submit.isSuccess && (
          <p aria-live="polite">
            {submittedNames
              ? `已交给 ${submittedNames} 审批`
              : "暂无审批人，请联系企业管理员"}
          </p>
        )}
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
            <p className="text-sm">
              {request.start_week
                ? `${request.start_week} 至 ${request.end_week}`
                : "批准当周"}
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
      <section
        aria-label="我的不可用日期"
        className="max-w-xl space-y-3 rounded-xl border p-5"
      >
        <h2 className="text-lg font-semibold">我的不可用日期</h2>
        <Unavailability
          orgId={orgId}
          employeeId={query.data.employee_id}
          startWeek={query.data.weeks[0].week_start}
          canWrite
        />
      </section>
      <MyAssignments orgId={orgId} />
    </div>
  )
}

export function Unavailability({
  orgId,
  employeeId,
  startWeek,
  canWrite,
}: {
  orgId: string
  employeeId: string
  startWeek: string
  canWrite: boolean
}) {
  const client = useQueryClient()
  const [start, setStart] = useState(startWeek)
  const [end, setEnd] = useState(startWeek)
  const [over, setOver] = useState<string[]>([])
  const query = useQuery({
    queryKey: ["capacity", orgId, "unavailability", employeeId],
    queryFn: async () =>
      (
        await CapacityService.listUnavailability({
          path: { org_id: orgId },
          query: {
            employee_id: employeeId,
            from: "1970-01-01",
            to: "9999-12-31",
          },
        })
      ).data,
  })
  const save = useMutation({
    mutationFn: (id?: string) =>
      id
        ? CapacityService.deleteUnavailability({
            path: { org_id: orgId, unavailability_id: id },
          })
        : CapacityService.registerUnavailability({
            path: { org_id: orgId },
            body: { employee_id: employeeId, start_date: start, end_date: end },
          }),
    onSuccess: (response) => {
      setOver(response.data.over_weeks)
      client.invalidateQueries({ queryKey: ["capacity", orgId] })
    },
  })
  if (query.isPending) return <ProfileLoading />
  if (query.isError) return <ProfileError retry={() => query.refetch()} />
  return (
    <div className="space-y-4">
      {!query.data.count && (
        <p className="text-sm text-muted-foreground">暂无不可用日期。</p>
      )}
      {query.data.data.map((row) => (
        <div
          key={row.id}
          className="flex flex-wrap items-center justify-between gap-2 rounded-md border p-3 text-sm"
        >
          <span>
            {row.start_date} 至 {row.end_date}
          </span>
          {row.can_delete && (
            <Button
              variant="outline"
              disabled={save.isPending}
              onClick={() => save.mutate(row.id)}
            >
              删除
            </Button>
          )}
        </div>
      ))}
      {canWrite && (
        <form
          className="space-y-3"
          onSubmit={(event) => {
            event.preventDefault()
            save.mutate(undefined)
          }}
        >
          <fieldset disabled={save.isPending} className="space-y-3">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor={`unavailable-start-${employeeId}`}>
                  开始日期
                </Label>
                <Input
                  id={`unavailable-start-${employeeId}`}
                  type="date"
                  required
                  value={start}
                  onChange={(event) => setStart(event.target.value)}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor={`unavailable-end-${employeeId}`}>
                  结束日期
                </Label>
                <Input
                  id={`unavailable-end-${employeeId}`}
                  type="date"
                  required
                  min={start}
                  value={end}
                  onChange={(event) => setEnd(event.target.value)}
                />
              </div>
            </div>
            <p className="text-sm text-muted-foreground">
              单次最多登记 92 天，无需填写原因（已有任务不会自动调整）。
            </p>
            <Button type="submit">
              {save.isPending ? "正在保存…" : "登记"}
            </Button>
          </fieldset>
        </form>
      )}
      {!!over.length && (
        <p role="status">
          以下周次已有任务超出可用负荷，请与部门经理协调：{over.join("、")}
        </p>
      )}
      <SetupError error={save.error} />
    </div>
  )
}
