import { useMutation, useQueryClient } from "@tanstack/react-query"
import { ChevronDown } from "lucide-react"
import { useState } from "react"
import {
  CapacityService,
  type ExtensionPublic,
  type WorkloadRequestPublic,
} from "@/client"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { ExtensionReviews } from "./ExtensionReviews"

export function Approvals({
  orgId,
  pendingWorkloads,
  pendingExtensions,
}: {
  orgId: string
  pendingWorkloads: WorkloadRequestPublic[]
  pendingExtensions: ExtensionPublic[]
}) {
  return (
    <div className="divide-y divide-surface-border">
      {!pendingWorkloads.length && !pendingExtensions.length && (
        <p role="status" className="p-3 text-sm text-muted-foreground">
          暂无待审批的负荷调整
        </p>
      )}
      <section
        aria-label="待处理的调整申请"
        className="divide-y divide-surface-border"
      >
        <h2 className="sr-only">待处理的调整申请</h2>
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
    <details className="group">
      <summary className="flex min-h-11 cursor-pointer list-none flex-wrap items-center gap-x-3 gap-y-1 px-3 py-1.5 text-sm hover:bg-muted/60 [&::-webkit-details-marker]:hidden">
        <span className="rounded-full border border-surface-border bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground">
          负荷调整
        </span>
        <span className="font-medium">
          {request.employee_name || "员工"} · 申请 {request.requested_percent}%
        </span>
        <span className="text-xs text-muted-foreground">
          {request.start_week
            ? `${request.start_week} 至 ${request.end_week}`
            : "批准当周"}
        </span>
        <span className="ml-auto inline-flex items-center gap-1 text-primary">
          处理{" "}
          <ChevronDown
            aria-hidden="true"
            className="size-4 group-open:rotate-180"
          />
        </span>
      </summary>
      <article
        aria-label="员工的调整申请"
        className="space-y-3 border-surface-border border-t p-3"
      >
        {request.on_behalf_of_name && (
          <p className="text-sm text-muted-foreground">
            代 {request.on_behalf_of_name} 审批（原审批人：
            {request.on_behalf_of_name}）
          </p>
        )}
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
          <Button
            disabled={decide.isPending}
            onClick={() => decide.mutate(true)}
          >
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
    </details>
  )
}
