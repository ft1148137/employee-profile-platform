import { useMutation, useQuery } from "@tanstack/react-query"
import { useState } from "react"
import {
  type ActiveBorrowAssignment,
  type BorrowDecisionInput,
  type BorrowRequest,
  CapacityService,
  CapacityTrackingService,
} from "@/client"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { Button } from "@/components/ui/button"
import { useBorrowRequests } from "@/hooks/usePendingApprovals"
import {
  AssignmentFeedback,
  useFeedbackAssignments,
} from "./AssignmentFeedback"
import { CapacityError, useCapacityRefresh } from "./AssignmentFields"
import { AssignmentTracking } from "./AssignmentTracking"

const statuses: Record<string, string> = {
  pending_selection: "待选人",
  selected: "已安排人员，等待本人同意",
  pending_consent: "原部门经理已同意，等待你确认",
  waiting_commitment: "你已同意，等待承诺生效",
  failed: "借调未达成",
}

export type RejectionReason = NonNullable<BorrowDecisionInput["reason_code"]>

const rejectionReasons: Record<RejectionReason, string> = {
  capacity: "人手或余量不足",
  skill: "能力不匹配",
  timing: "时间不合适",
  priority: "与其他工作冲突",
  scope: "需求不清或范围变化",
  other: "其他",
}

export function rejectionReasonLabel(code: string | null | undefined) {
  return (
    Object.entries(rejectionReasons).find(([key]) => key === code)?.[1] ??
    "未分类"
  )
}

export function RejectionReasonSelect({
  value,
  onChange,
}: {
  value: RejectionReason | ""
  onChange: (value: RejectionReason | "") => void
}) {
  return (
    <label className="block space-y-2 text-sm font-medium">
      <span>拒绝原因</span>
      <select
        className="h-10 w-full rounded-md border bg-background px-3"
        value={value}
        onChange={(event) =>
          onChange(event.target.value as RejectionReason | "")
        }
      >
        <option value="">请选择原因</option>
        {Object.entries(rejectionReasons).map(([code, label]) => (
          <option key={code} value={code}>
            {label}
          </option>
        ))}
      </select>
    </label>
  )
}

export function BorrowRequests({
  orgId,
  mine = false,
}: {
  orgId: string
  mine?: boolean
}) {
  const query = useBorrowRequests(orgId, mine)
  if (query.isPending) return <ProfileLoading />
  if (query.isError) return <ProfileError retry={() => query.refetch()} />
  if (mine && query.data.length === 0) return null
  return (
    <section aria-label="借调请求" className="space-y-3">
      <h2 className="text-lg font-semibold">借调请求</h2>
      {!query.data.length && (
        <p className="text-muted-foreground">暂无借调请求。</p>
      )}
      {query.data.map((request) => (
        <BorrowRequestCard
          key={request.id}
          orgId={orgId}
          request={request}
          mine={mine}
        />
      ))}
      {!mine && (
        <>
          <ActiveBorrowAssignments orgId={orgId} />
          <CompletedBorrowAssignments orgId={orgId} />
        </>
      )}
    </section>
  )
}

function ActiveBorrowAssignments({ orgId }: { orgId: string }) {
  const assignments = useQuery({
    queryKey: ["capacity", orgId, "active-borrow-assignments"],
    queryFn: async () =>
      (
        await CapacityTrackingService.trackingListActiveBorrowAssignments({
          path: { org_id: orgId },
        })
      ).data,
  })
  if (assignments.isPending) return <ProfileLoading />
  if (assignments.isError)
    return <ProfileError retry={() => assignments.refetch()} />
  if (!assignments.data.length) return null
  return (
    <section aria-label="进行中的借调分配" className="space-y-3">
      <h2 className="text-lg font-semibold">进行中的借调分配</h2>
      {assignments.data.map((assignment) => (
        <ActiveBorrowAssignmentCard
          key={assignment.id}
          orgId={orgId}
          assignment={assignment}
        />
      ))}
    </section>
  )
}

function ActiveBorrowAssignmentCard({
  orgId,
  assignment,
}: {
  orgId: string
  assignment: ActiveBorrowAssignment
}) {
  const refresh = useCapacityRefresh(orgId)
  const complete = useMutation({
    mutationFn: () =>
      CapacityService.updateAssignment({
        path: { org_id: orgId, assignment_id: assignment.id },
        body: { status: "done" },
      }),
    onSuccess: refresh,
  })
  return (
    <article className="space-y-2 rounded-xl border p-4">
      <h3 className="font-medium">{assignment.employee_name}</h3>
      <p>
        {assignment.demand_title} ·{" "}
        {assignment.start_date && `${assignment.start_date} 至 `}
        {assignment.due_date}
      </p>
      <AssignmentTracking orgId={orgId} assignmentId={assignment.id} />
      <Button
        variant="outline"
        disabled={complete.isPending || complete.isSuccess}
        onClick={() => complete.mutate()}
      >
        标记完成
      </Button>
      <CapacityError error={complete.error} />
    </article>
  )
}

function CompletedBorrowAssignments({ orgId }: { orgId: string }) {
  const assignments = useFeedbackAssignments(orgId)
  if (assignments.isPending) return <ProfileLoading />
  if (assignments.isError)
    return <ProfileError retry={() => assignments.refetch()} />
  if (!assignments.data.length) return null
  return (
    <section aria-label="已结束的借调分配" className="space-y-3">
      <h2 className="text-lg font-semibold">已结束的借调分配</h2>
      {assignments.data.map((assignment) => (
        <article
          key={assignment.id}
          className="space-y-2 rounded-xl border p-4"
        >
          <h3 className="font-medium">{assignment.employee_name}</h3>
          <p>
            {assignment.demand_title} · {assignment.due_date}
          </p>
          <AssignmentFeedback orgId={orgId} assignment={assignment} />
        </article>
      ))}
    </section>
  )
}

export function BorrowRequestCard({
  orgId,
  request,
  mine,
}: {
  orgId: string
  request: BorrowRequest
  mine: boolean
}) {
  const [employee, setEmployee] = useState("")
  const [reason, setReason] = useState("")
  const [reasonCode, setReasonCode] = useState<RejectionReason | "">("")
  const refresh = useCapacityRefresh(orgId)
  const mutation = useMutation({
    mutationFn: (body: BorrowDecisionInput) =>
      CapacityService.decideBorrowRequest({
        path: { org_id: orgId, line_id: request.id },
        body,
      }),
    onSuccess: refresh,
    onError: refresh,
  })
  const selectable = request.status === "pending_selection"
  const consent = request.status === "pending_consent"
  return (
    <article className="space-y-3 rounded-xl border p-5">
      <h3 className="font-semibold">
        {request.capability_name} · {statuses[request.status]}
      </h3>
      <p className="text-sm">
        借入部门：{request.borrower_department} · 投入{" "}
        {request.allocation_percent}% · {request.start_date} 至{" "}
        {request.end_date}
      </p>
      <p className="whitespace-pre-wrap break-words">{request.description}</p>
      {request.reason && (
        <p>
          你的拒绝理由：{rejectionReasonLabel(request.reason_code)} ·{" "}
          {request.reason}
        </p>
      )}
      {(selectable || consent) && (
        <fieldset className="space-y-3" disabled={mutation.isPending}>
          {selectable && (
            <label className="block space-y-2 text-sm">
              <span>借调员工</span>
              <select
                className="h-10 w-full rounded-md border bg-background px-3"
                value={employee}
                onChange={(event) => setEmployee(event.target.value)}
              >
                <option value="">请选择</option>
                {request.candidates?.map((person) => (
                  <option key={person.employee_id} value={person.employee_id}>
                    {person.display_name}
                  </option>
                ))}
              </select>
            </label>
          )}
          <RejectionReasonSelect value={reasonCode} onChange={setReasonCode} />
          <label className="block space-y-2 text-sm">
            <span>
              拒绝理由
              {mine && reasonCode !== "other"
                ? "（选填，仅本人可见）"
                : "（拒绝时必填）"}
            </span>
            <textarea
              className="min-h-16 w-full rounded-md border bg-background p-3"
              maxLength={500}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </label>
          <div className="flex flex-wrap gap-3">
            <Button
              disabled={selectable && !employee}
              onClick={() =>
                mutation.mutate(
                  selectable
                    ? { action: "select", employee_id: employee }
                    : { action: "accept" },
                )
              }
            >
              {selectable ? "确认人选" : "同意"}
            </Button>
            <Button
              variant="outline"
              disabled={
                !reasonCode ||
                ((!mine || reasonCode === "other") && !reason.trim())
              }
              onClick={() =>
                mutation.mutate({
                  action: mine ? "decline" : "reject",
                  reason_code: reasonCode || null,
                  reason: reason.trim() || null,
                })
              }
            >
              {mine ? "不同意" : "拒绝借调"}
            </Button>
          </div>
        </fieldset>
      )}
      <CapacityError error={mutation.error} />
    </article>
  )
}
