import { useMutation } from "@tanstack/react-query"
import {
  CapacityService,
  type ExtensionPublic,
  type MyAssignment,
} from "@/client"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
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
  return (
    <section aria-label="我的分配" className="space-y-4">
      <h2 className="text-lg font-semibold">我的分配</h2>
      <BorrowRequests orgId={orgId} mine />
      {!assignments.data.count && (
        <p className="rounded-xl border border-dashed p-8 text-center text-muted-foreground">
          暂无分配。
        </p>
      )}
      {assignments.data.data.map((row) => (
        <MyAssignmentRow
          key={row.id}
          orgId={orgId}
          row={row}
          requests={requests.data.data.filter(
            (request) => request.assignment_id === row.id,
          )}
        />
      ))}
    </section>
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
    <article className="space-y-3 rounded-xl border p-5">
      <h3 className="font-semibold">
        {row.capability_name} · {assignmentStatuses[row.status]}
      </h3>
      <p className="text-sm">
        需求方：{row.requesting_department_name} · 投入 {row.allocation_percent}
        % · {row.start_date ? `开始：${row.start_date}` : "开始日期未确认"} ·
        预计完成：{row.due_date}
      </p>
      <p className="whitespace-pre-wrap break-words">{row.description}</p>
      {row.status === "active" && (
        <AssignmentTracking orgId={orgId} assignmentId={row.id} />
      )}
      {pending && (
        <p role="status">
          {waitingNames
            ? `已提交，等待 ${waitingNames} 审批`
            : "暂未配置对应审批人，请联系企业管理员"}
        </p>
      )}
      {!pending && row.status === "active" && (
        <form
          aria-label="申请延期"
          onSubmit={(event) => {
            event.preventDefault()
            submit.mutate(new FormData(event.currentTarget))
          }}
        >
          <h4 className="font-medium">申请延期</h4>
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
      )}
      <CapacityError error={submit.error} />
      <section aria-label="延期申请记录" className="space-y-2">
        <h4 className="text-sm font-medium">延期申请记录</h4>
        {!requests.length && (
          <p className="text-sm text-muted-foreground">暂无延期申请。</p>
        )}
        {requests.map((request) => (
          <div key={request.id} className="space-y-1 border-l-2 pl-3 text-sm">
            <p>
              {request.requested_due_date} · {extensionStatuses[request.status]}
            </p>
            <p className="whitespace-pre-wrap break-words">{request.reason}</p>
            {request.decision_note && (
              <p className="whitespace-pre-wrap break-words">
                处理说明：{request.decision_note}
              </p>
            )}
          </div>
        ))}
      </section>
    </article>
  )
}
