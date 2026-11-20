import { useMutation } from "@tanstack/react-query"
import { ChevronRight } from "lucide-react"
import {
  CapacityService,
  type ExtensionPublic,
  type MyAssignment,
} from "@/client"
import { SectionCard } from "@/components/Common/WorkbenchPrimitives"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  CapacityError,
  DateField,
  NoteField,
  useCapacityRefresh,
  useExtensions,
  useMyAssignments,
} from "./AssignmentFields"
import { AssignmentTracking } from "./AssignmentTracking"
import { BorrowRequests } from "./BorrowRequests"
import { assignmentStatuses } from "./DemandAssignments"

const extensionStatuses = {
  pending: "待处理",
  approved: "已同意",
  returned: "已退回",
}

export function MyAssignments({ orgId }: { orgId: string }) {
  const assignments = useMyAssignments(orgId)
  const requests = useExtensions(orgId)
  if (assignments.isPending || requests.isPending) return <ProfileLoading />
  if (assignments.isError || requests.isError)
    return (
      <ProfileError
        retry={() => {
          assignments.refetch()
          requests.refetch()
        }}
      />
    )
  const groups = new Map<string, MyAssignment[]>([
    ["进行中", []],
    ["即将开始", []],
    ["历史分配", []],
  ])
  const today = new Date().toLocaleDateString("sv-SE")
  for (const row of assignments.data.data) {
    const group =
      row.status !== "active"
        ? "历史分配"
        : row.start_date && row.start_date > today
          ? "即将开始"
          : "进行中"
    groups.get(group)?.push(row)
  }
  return (
    <SectionCard aria-label="我的分配" className="space-y-3 p-0">
      <h2 className="px-4 pt-4 text-sm font-semibold">
        我参与的任务与借调分配
      </h2>
      <div className="px-4 empty:hidden">
        <BorrowRequests orgId={orgId} mine />
      </div>
      {!assignments.data.count && (
        <p className="px-4 pb-4 text-sm text-muted-foreground">暂无分配。</p>
      )}
      {Array.from(groups, ([label, rows]) =>
        rows.length ? (
          <section key={label} aria-label={label}>
            <h3 className="border-y bg-surface-muted px-4 py-1.5 text-xs font-medium text-muted-foreground">
              {label}（{rows.length}）
            </h3>
            <div className="divide-y">
              {rows.map((row) => (
                <MyAssignmentRow
                  key={row.id}
                  orgId={orgId}
                  row={row}
                  requests={requests.data.data.filter(
                    (request) => request.assignment_id === row.id,
                  )}
                />
              ))}
            </div>
          </section>
        ) : null,
      )}
    </SectionCard>
  )
}

function MyAssignmentRow({
  orgId,
  row,
  requests,
}: {
  orgId: string
  row: MyAssignment
  requests: ExtensionPublic[]
}) {
  const refresh = useCapacityRefresh(orgId)
  const submit = useMutation({
    mutationFn: (data: FormData) =>
      CapacityService.requestExtension({
        path: { org_id: orgId, assignment_id: row.id },
        body: {
          requested_due_date: String(data.get("requested_due_date")),
          reason: String(data.get("reason")).trim(),
        },
      }),
    onSuccess: refresh,
  })
  const pending = requests.some((request) => request.status === "pending")
  const waitingNames = (
    submit.data?.data.reviewer_names ?? row.reviewer_names
  ).join("、")
  const nextDueDate = new Date(Date.parse(row.due_date) + 86_400_000)
    .toISOString()
    .slice(0, 10)
  return (
    <article className="space-y-2 px-4 py-2.5 text-sm">
      <div className="flex min-h-6 flex-wrap items-center gap-x-3 gap-y-1">
        <h4 className="min-w-0 flex-1 break-words font-semibold">
          {row.capability_name}
        </h4>
        <Badge variant="secondary" className="max-w-full whitespace-normal">
          {row.requesting_department_name}
        </Badge>
        <span className="font-semibold tabular-nums">
          {row.allocation_percent}%
        </span>
        <span className="text-xs text-muted-foreground">
          {row.start_date ?? "开始日期未确认"} 至 {row.due_date}
        </span>
        <Badge variant="outline">{assignmentStatuses[row.status]}</Badge>
      </div>
      <details className="group">
        <summary className="inline-flex cursor-pointer items-center gap-1 text-xs font-medium text-primary">
          <ChevronRight
            className="size-3 group-open:rotate-90"
            aria-hidden="true"
          />
          任务明细
        </summary>
        <p className="my-2 whitespace-pre-wrap break-words">
          {row.description}
        </p>
        {row.status === "active" && (
          <AssignmentTracking orgId={orgId} assignmentId={row.id} />
        )}
      </details>
      {pending && (
        <p role="status">
          {waitingNames
            ? `已提交，等待 ${waitingNames} 审批`
            : "暂未配置对应审批人，请联系企业管理员"}
        </p>
      )}
      {!pending && row.status === "active" && (
        <details className="group">
          <summary className="inline-flex cursor-pointer items-center gap-1 text-xs font-medium text-primary">
            <ChevronRight
              className="size-3 group-open:rotate-90"
              aria-hidden="true"
            />
            申请延期
          </summary>
          <form
            className="mt-2 space-y-2 rounded-md border bg-surface-muted p-3"
            aria-label="申请延期"
            onSubmit={(event) => {
              event.preventDefault()
              submit.mutate(new FormData(event.currentTarget))
            }}
          >
            <p className="text-sm text-muted-foreground">
              {row.reviewer_names.length
                ? `将交给 ${row.reviewer_names.join("、")} 审批`
                : "暂未配置对应审批人，请联系企业管理员"}
            </p>
            <fieldset disabled={submit.isPending} className="space-y-3">
              <DateField
                label="申请完成日期"
                name="requested_due_date"
                min={nextDueDate}
              />
              <NoteField label="延期原因" name="reason" />
              <Button type="submit">提交延期申请</Button>
            </fieldset>
          </form>
        </details>
      )}
      <CapacityError error={submit.error} />
      {!!requests.length && (
        <section aria-label="延期申请记录" className="space-y-2">
          <h4 className="text-xs font-medium text-muted-foreground">
            延期申请记录
          </h4>
          {requests.map((request) => (
            <div key={request.id} className="space-y-1 border-l-2 pl-3 text-sm">
              <p>
                {request.requested_due_date} ·{" "}
                {extensionStatuses[request.status]}
              </p>
              <p className="whitespace-pre-wrap break-words">
                {request.reason}
              </p>
              {request.decision_note && (
                <p className="whitespace-pre-wrap break-words">
                  处理说明：{request.decision_note}
                </p>
              )}
            </div>
          ))}
        </section>
      )}
    </article>
  )
}
