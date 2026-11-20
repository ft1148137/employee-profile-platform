import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Plus } from "lucide-react"
import { type ReactNode, useState } from "react"
import { CapacityService, type MyWorkload as MyWorkloadData } from "@/client"
import { TopbarActions } from "@/components/Common/TopbarSlot"
import {
  SectionCard,
  SideDrawer,
} from "@/components/Common/WorkbenchPrimitives"
import { dateLabel } from "@/components/Profile/fields"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { cn } from "@/lib/utils"
import { MyAssignments } from "./MyAssignments"
import {
  addWeeks,
  loadBandLabel,
  loadColors,
  occupiedPercent,
  UtilizationBadge,
  unavailableStripes,
  WorkloadHelp,
  weekBand,
  weekLabel,
} from "./shared"

export function MyWorkload({
  orgId,
  actions,
}: {
  orgId: string
  actions: ReactNode
}) {
  const client = useQueryClient()
  const [adjusting, setAdjusting] = useState(false)
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
      setPercent("")
      setReason("")
      setStart("")
      setEnd("")
      setAdjusting(false)
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
    <div className="space-y-3">
      <TopbarActions>
        {actions}
        <WorkloadHelp />
        <SideDrawer
          title="申请调整"
          open={adjusting}
          onOpenChange={setAdjusting}
          trigger={
            <Button size="sm" disabled={submit.isPending}>
              <Plus aria-hidden="true" className="hidden sm:block" />
              申请调整
            </Button>
          }
        >
          <form
            aria-label="申请调整"
            className="space-y-4"
            onSubmit={(event) => {
              event.preventDefault()
              submit.mutate()
            }}
          >
            <p className="text-sm text-muted-foreground">
              填写平台外工作的精力占比（0–200%），平台内已分配任务会自动累加。
            </p>
            <p className="text-sm text-muted-foreground">
              {reviewerNames
                ? `将交给 ${reviewerNames} 审批`
                : "暂无审批人，请联系企业管理员"}
            </p>
            <fieldset
              disabled={pending || submit.isPending}
              className="space-y-4"
            >
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
            <SetupError error={submit.error} />
          </form>
        </SideDrawer>
      </TopbarActions>
      <WorkloadOverview workload={query.data} />
      <div className="grid items-start gap-3 lg:grid-cols-[minmax(0,68fr)_minmax(0,32fr)]">
        <MyAssignments orgId={orgId} />
        <aside className="min-w-0 space-y-3">
          <SectionCard aria-label="调整申请记录" className="space-y-3">
            <h2 className="text-sm font-semibold">最近调整申请记录</h2>
            {pending && (
              <p className="text-sm" role="status">
                当前有申请正在审批中，请等待处理。
              </p>
            )}
            {submit.isSuccess && (
              <p className="text-sm" aria-live="polite">
                {submittedNames
                  ? `已交给 ${submittedNames} 审批`
                  : "暂无审批人，请联系企业管理员"}
              </p>
            )}
            {!query.data.requests.length && (
              <p className="text-sm text-muted-foreground">暂无调整申请。</p>
            )}
            <div className="divide-y">
              {query.data.requests.map((request) => (
                <article key={request.id} className="space-y-1 py-2.5 text-sm">
                  <div className="flex flex-wrap items-center justify-between gap-1">
                    <p className="font-medium">
                      {request.requested_percent}% ·{" "}
                      {
                        {
                          pending: "待处理",
                          approved: "已同意",
                          returned: "已退回",
                        }[request.status]
                      }
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {dateLabel(request.created_at)}
                    </p>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {request.start_week
                      ? `${request.start_week} 至 ${request.end_week}`
                      : "批准当周"}
                  </p>
                  <p className="whitespace-pre-wrap break-words">
                    {request.reason}
                  </p>
                  {request.decision_note && (
                    <p className="whitespace-pre-wrap break-words">
                      处理说明：{request.decision_note}
                    </p>
                  )}
                </article>
              ))}
            </div>
          </SectionCard>
          <SectionCard aria-label="我的不可用日期" className="space-y-3">
            <h2 className="text-sm font-semibold">我的不可用日期</h2>
            <Unavailability
              orgId={orgId}
              employeeId={query.data.employee_id}
              startWeek={query.data.weeks[0].week_start}
              canWrite
            />
          </SectionCard>
        </aside>
      </div>
    </div>
  )
}

function WorkloadOverview({ workload }: { workload: MyWorkloadData }) {
  const overload = workload.weeks.find(
    (week) => weekBand(week) === "overloaded",
  )
  return (
    <SectionCard aria-label="每周投入" className="space-y-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <h2 className="text-sm font-semibold">未来 8 周负荷概览</h2>
        {overload && (
          <span className="rounded-full bg-red-500/10 px-2 py-0.5 text-xs text-red-700 dark:text-red-300">
            {weekLabel(overload.week_start)}预计超负荷
          </span>
        )}
        <ul
          aria-label="利用率图例"
          className="ml-auto flex flex-wrap gap-x-3 text-xs text-muted-foreground"
        >
          {(["available", "full", "overloaded"] as const).map((band) => (
            <li key={band} className="flex items-center gap-1">
              <span
                className={cn("size-2 rounded-full", loadColors[band].bar)}
              />
              {loadBandLabel(band)}
              {band === "available"
                ? "（<80%）"
                : band === "full"
                  ? "（80–100%）"
                  : "（超可用）"}
            </li>
          ))}
        </ul>
      </div>
      <div className="grid grid-cols-2 gap-2 min-[540px]:grid-cols-4 xl:grid-cols-8">
        {workload.weeks.map((week, index) => {
          const band = weekBand(week)
          const total = occupiedPercent(week)
          const remaining = week.capacity_percent - total
          const unavailable =
            week.capacity_percent < 100 ||
            (workload.unavailabilities ?? []).some(
              (dates) =>
                dates.start_date < addWeeks(week.week_start, 1) &&
                dates.end_date >= week.week_start,
            )
          const colors = loadColors[band]
          return (
            <div
              key={week.week_start}
              className={cn(
                "min-w-0 space-y-1.5 rounded-md border p-2.5 text-xs",
                colors.surface,
                unavailable && unavailableStripes,
              )}
            >
              <div className="flex flex-wrap items-center justify-between gap-1">
                <time className="font-semibold" dateTime={week.week_start}>
                  {index === 0
                    ? "本周"
                    : index === 1
                      ? "下周"
                      : weekLabel(week.week_start)}
                </time>
                {week.status === "stale" ? (
                  "需重新确认"
                ) : (
                  <UtilizationBadge band={band} percent={total} />
                )}
              </div>
              {index < 2 && (
                <p className="text-muted-foreground">
                  {weekLabel(week.week_start)}
                </p>
              )}
              <div
                aria-hidden="true"
                className="relative h-1.5 rounded-full bg-foreground/10"
              >
                <span
                  className={cn("block h-full rounded-full", colors.bar)}
                  style={{ width: `${Math.min(total, 100)}%` }}
                />
                <span
                  className="absolute -inset-y-0.5 w-px bg-red-500"
                  style={{ left: `calc(${week.capacity_percent}% - 1px)` }}
                />
              </div>
              {band !== "unconfirmed" && (
                <p
                  className={
                    remaining < 0
                      ? "font-semibold text-red-700 dark:text-red-300"
                      : "text-muted-foreground"
                  }
                >
                  {remaining < 0
                    ? `超载 +${-remaining}%`
                    : `余量 ${remaining}%`}
                </p>
              )}
              {unavailable && (
                <p>有不可用日期 · 可用 {week.capacity_percent}%</p>
              )}
              {workload.correction_pending[week.week_start] && (
                <p>调整审批中</p>
              )}
              {week.legacy_start && <p>开始日期未确认</p>}
            </div>
          )
        })}
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t pt-2 text-xs text-muted-foreground">
        <span>
          Jira 参考值 ·{" "}
          <span>
            {workload.jira_task_count === null
              ? "暂无（未关联 Jira）"
              : `${workload.jira_task_count} 件未完成`}
          </span>
        </span>
      </div>
    </SectionCard>
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
    <div className="@container space-y-3">
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
            <div className="grid min-w-0 gap-3 @sm:grid-cols-2">
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
