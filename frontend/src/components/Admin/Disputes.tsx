import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import { useState } from "react"
import { type AppealPublic, AppealsService } from "@/client"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import { useOrg } from "@/hooks/useOrg"

export function Disputes({
  orgId,
  employeeId,
}: {
  orgId: string
  employeeId?: string
}) {
  const { employeeId: currentEmployeeId } = useOrg()
  const query = useQuery({
    queryKey: ["disputes", orgId, employeeId],
    queryFn: async () =>
      (
        await AppealsService.listDisputes({
          path: { org_id: orgId },
          query: { employee_id: employeeId },
        })
      ).data,
  })
  if (query.isPending) return <ProfileLoading />
  if (query.isError) return <ProfileError retry={() => query.refetch()} />
  return (
    <div className="mx-auto max-w-3xl space-y-5">
      <h1 className="text-2xl font-semibold">争议</h1>
      <p>
        处理申诉不会直接批准改动。允许重新提交时，员工修改草稿后照常提交审批。
      </p>
      <p role="status">{query.data.length} 项待处理申诉</p>
      {query.data.map((appeal) => (
        <Resolve
          key={appeal.id}
          orgId={orgId}
          appeal={appeal}
          isOwn={appeal.employee_id === currentEmployeeId}
        />
      ))}
    </div>
  )
}
function Resolve({
  orgId,
  appeal,
  isOwn,
}: {
  orgId: string
  appeal: AppealPublic
  isOwn: boolean
}) {
  const [reason, setReason] = useState("")
  const client = useQueryClient()
  const resolve = useMutation({
    mutationFn: (status: "upheld" | "reopened") =>
      AppealsService.resolveAppeal({
        path: { org_id: orgId, appeal_id: appeal.id },
        body: { status, reason },
      }),
    onSuccess: () => client.invalidateQueries(),
  })
  return (
    <section
      aria-label="待处理申诉"
      className="space-y-3 rounded-xl border p-5"
    >
      <Link
        className="text-primary underline"
        to="/employees/$employeeId"
        params={{ employeeId: appeal.employee_id }}
      >
        查看员工画像与原请求
      </Link>
      <p className="whitespace-pre-wrap break-words">
        申诉原因：{appeal.reason}
      </p>
      <SetupError error={resolve.error} />
      {isOwn && (
        <p id={`self-appeal-${appeal.id}`}>
          不能处理自己提出的申诉，请由另一位企业管理员处理
        </p>
      )}
      <label className="block" htmlFor={`resolution-${appeal.id}`}>
        处理说明
        <textarea
          className="w-full rounded-md border bg-background p-3"
          id={`resolution-${appeal.id}`}
          aria-label="处理说明"
          maxLength={4000}
          value={reason}
          onChange={(event) => setReason(event.target.value)}
        />
      </label>
      <div className="flex flex-wrap gap-3">
        {(["upheld", "reopened"] as const).map((status) => (
          <Button
            key={status}
            disabled={isOwn || !reason.trim() || resolve.isPending}
            aria-describedby={isOwn ? `self-appeal-${appeal.id}` : undefined}
            onClick={() => resolve.mutate(status)}
          >
            {status === "upheld" ? "维持原决定" : "允许重新提交"}
          </Button>
        ))}
      </div>
    </section>
  )
}
