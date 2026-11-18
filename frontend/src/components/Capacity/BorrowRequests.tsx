import { useMutation, useQuery } from "@tanstack/react-query"
import { useState } from "react"
import {
  type BorrowDecisionInput,
  type BorrowRequest,
  CapacityService,
} from "@/client"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { Button } from "@/components/ui/button"
import { CapacityError, useCapacityRefresh } from "./AssignmentFields"

const statuses: Record<string, string> = {
  pending_selection: "待选人",
  selected: "已安排人员，等待本人同意",
  pending_consent: "原部门经理已同意，等待你确认",
  waiting_commitment: "你已同意，等待承诺生效",
  failed: "借调未达成",
}

export function BorrowRequests({
  orgId,
  mine = false,
}: {
  orgId: string
  mine?: boolean
}) {
  const query = useQuery({
    queryKey: ["capacity", orgId, "borrows", mine],
    queryFn: async () =>
      (
        await CapacityService.listBorrowRequests({
          path: { org_id: orgId },
          query: { mine },
        })
      ).data,
  })
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
    </section>
  )
}

function BorrowRequestCard({
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
      {request.reason && <p>你的拒绝理由：{request.reason}</p>}
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
          <label className="block space-y-2 text-sm">
            <span>
              拒绝理由{mine ? "（选填，仅本人可见）" : "（拒绝时必填）"}
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
              disabled={!mine && !reason.trim()}
              onClick={() =>
                mutation.mutate({
                  action: mine ? "decline" : "reject",
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
