import { useMutation } from "@tanstack/react-query"
import { useState } from "react"
import {
  CapacityService,
  type ExtensionDecisionInput,
  type ExtensionPublic,
} from "@/client"
import { Button } from "@/components/ui/button"
import {
  CapacityError,
  CommitmentFields,
  commitmentBody,
  NoteField,
  useCapacityRefresh,
} from "./AssignmentFields"

export function ExtensionReviews({
  orgId,
  requests,
}: {
  orgId: string
  requests: ExtensionPublic[]
}) {
  return (
    <section aria-label="延期审批" className="space-y-3">
      <h2 className="text-lg font-semibold">延期审批</h2>
      {!requests.length && (
        <p className="text-muted-foreground">暂无待处理的延期申请。</p>
      )}
      {requests.map((request) => (
        <ExtensionDecision key={request.id} orgId={orgId} request={request} />
      ))}
    </section>
  )
}

function ExtensionDecision({
  orgId,
  request,
}: {
  orgId: string
  request: ExtensionPublic
}) {
  const refresh = useCapacityRefresh(orgId)
  const [approve, setApprove] = useState(true)
  const decide = useMutation({
    mutationFn: (body: ExtensionDecisionInput) =>
      CapacityService.decideExtensionRequest({
        path: { org_id: orgId, request_id: request.id },
        body,
      }),
    onSuccess: refresh,
  })
  return (
    <form
      aria-label={`${request.employee_name}的延期申请`}
      className="space-y-3 rounded-xl border p-5"
      onSubmit={(event) => {
        event.preventDefault()
        const data = new FormData(event.currentTarget)
        decide.mutate({
          approve,
          note: String(data.get("note")).trim() || null,
          ...(approve ? commitmentBody(data) : {}),
        })
      }}
    >
      <h3 className="font-semibold">
        {request.employee_name} · {request.capability_name}
      </h3>
      {request.on_behalf_of_name && (
        <p className="text-sm text-muted-foreground">
          代 {request.on_behalf_of_name} 审批（原审批人：
          {request.on_behalf_of_name}）
        </p>
      )}
      <p className="text-sm">
        当前完成：{request.current_due_date} → 申请完成：
        {request.requested_due_date} · 对外承诺：
        {request.committed_end_date ?? "未定"}
      </p>
      <p className="whitespace-pre-wrap break-words">{request.reason}</p>
      <fieldset disabled={decide.isPending} className="space-y-3">
        <label className="block space-y-2 text-sm">
          <span>处理方式</span>
          <select
            className="h-10 w-full rounded-md border bg-background px-3"
            value={approve ? "approve" : "return"}
            onChange={(event) => {
              setApprove(event.target.value === "approve")
              decide.reset()
            }}
          >
            <option value="approve">同意</option>
            <option value="return">退回</option>
          </select>
        </label>
        {approve && !!request.commitment_confirmer_names?.length && (
          <p className="text-sm rounded-lg border p-3">
            这次延期会推迟对需求方的承诺日期，你不能确认新的承诺日期，需要{" "}
            {request.commitment_confirmer_names.join("、")} 确认。可以：①{" "}
            {request.on_behalf_of_name
              ? `请原审批人 ${request.on_behalf_of_name} 把审批代理给 ${request.commitment_confirmer_names.join("、")}`
              : `在「设置 → 代理审批」把审批代理给 ${request.commitment_confirmer_names.join("、")}`}
            ， 对方会在「需求与容量 → 待我审批」里处理；②
            选择「退回」，请员工把完成日期改到 {request.committed_end_date}{" "}
            或之前再申请。代理设置适用于原审批人的全部审批，包括画像审批。
          </p>
        )}
        <NoteField
          label={approve ? "处理说明" : "处理说明（退回必填）"}
          name="note"
          required={!approve}
        />
        {approve && (
          <CommitmentFields
            dueDate={request.requested_due_date}
            committedEndDate={request.committed_end_date}
          />
        )}
        <Button type="submit">{approve ? "同意延期" : "退回延期"}</Button>
      </fieldset>
      <CapacityError error={decide.error} />
    </form>
  )
}
