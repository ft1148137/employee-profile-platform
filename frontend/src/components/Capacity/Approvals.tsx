import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { CapacityService, type WorkloadRequestPublic } from "@/client"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { useOrg } from "@/hooks/useOrg"
import { useExtensions, useMyAssignments } from "./AssignmentFields"
import { ExtensionReviews } from "./ExtensionReviews"

export function Approvals({ orgId }: { orgId: string }) {
  const { employeeId } = useOrg()
  const workloads = useQuery({
    queryKey: ["capacity", orgId, "requests"],
    queryFn: async () =>
      (await CapacityService.listWorkloadRequests({ path: { org_id: orgId } }))
        .data,
  })
  const extensions = useExtensions(orgId)
  const own = useMyAssignments(orgId, !!employeeId)
  if (
    workloads.isPending ||
    extensions.isPending ||
    (employeeId && own.isPending)
  )
    return <ProfileLoading />
  if (workloads.isError || extensions.isError || own.isError)
    return (
      <ProfileError
        retry={() => {
          workloads.refetch()
          extensions.refetch()
          if (employeeId) own.refetch()
        }}
      />
    )
  const pendingWorkloads = workloads.data.data.filter(
    (row) => row.status === "pending",
  )
  const pendingExtensions = extensions.data.data.filter(
    (row) =>
      row.status === "pending" &&
      !own.data?.data.some((item) => item.id === row.assignment_id),
  )
  return (
    <div className="space-y-6">
      {!pendingWorkloads.length && !pendingExtensions.length && (
        <p role="status" className="text-muted-foreground">
          目前没有需要你审批的申请
        </p>
      )}
      <section aria-label="待处理的调整申请" className="space-y-3">
        <h2 className="text-lg font-semibold">待处理的调整申请</h2>
        {pendingWorkloads.map((request) => (
          <WorkloadDecision key={request.id} orgId={orgId} request={request} />
        ))}
      </section>
      <ExtensionReviews orgId={orgId} requests={pendingExtensions} />
    </div>
  )
}

function WorkloadDecision({
  orgId,
  request,
}: {
  orgId: string
  request: WorkloadRequestPublic
}) {
  const client = useQueryClient()
  const [note, setNote] = useState("")
  const decide = useMutation({
    mutationFn: (approve: boolean) =>
      CapacityService.decideWorkloadRequest({
        path: { org_id: orgId, request_id: request.id },
        body: { approve, note: note.trim() || null },
      }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["capacity", orgId] })
      client.invalidateQueries({ queryKey: ["inbox", orgId] })
    },
  })
  return (
    <article
      aria-label="员工的调整申请"
      className="space-y-3 rounded-xl border p-4"
    >
      <h3 className="font-medium">员工 · 申请 {request.requested_percent}%</h3>
      <p className="whitespace-pre-wrap break-words">{request.reason}</p>
      <Label htmlFor={`decision-${request.id}`}>处理说明（退回必填）</Label>
      <textarea
        className="w-full rounded-md border bg-transparent p-3 text-sm"
        id={`decision-${request.id}`}
        maxLength={500}
        value={note}
        disabled={decide.isPending}
        onChange={(event) => setNote(event.target.value)}
      />
      <div className="flex gap-2">
        <Button disabled={decide.isPending} onClick={() => decide.mutate(true)}>
          同意
        </Button>
        <Button
          variant="outline"
          disabled={decide.isPending || !note.trim()}
          onClick={() => decide.mutate(false)}
        >
          退回
        </Button>
      </div>
      <SetupError error={decide.error} />
    </article>
  )
}
