import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { AppealsService, type ChangeItemPublic } from "@/client"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"

export function AppealItem({
  orgId,
  requestId,
  item,
}: {
  orgId: string
  requestId: string
  item: ChangeItemPublic
}) {
  const client = useQueryClient()
  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState("")
  const query = useQuery({
    queryKey: ["appeal", orgId, item.appeal_id],
    enabled: !!item.appeal_id,
    queryFn: async () =>
      (
        await AppealsService.readAppeal({
          path: { org_id: orgId, appeal_id: item.appeal_id! },
        })
      ).data,
  })
  const create = useMutation({
    mutationFn: () =>
      AppealsService.createAppeal({
        path: { org_id: orgId },
        body: { request_id: requestId, item_id: item.id, reason },
      }),
    onSuccess: () => {
      setOpen(false)
      setReason("")
      client.invalidateQueries()
    },
  })
  return (
    <div className="space-y-2">
      <SetupError error={create.error ?? query.error} />
      {query.data && (
        <p className="whitespace-pre-wrap break-words">
          申诉：
          {query.data.status === "open"
            ? "待处理"
            : query.data.status === "upheld"
              ? "维持原决定"
              : "允许重新提交"}
          {query.data.resolution_note && ` · ${query.data.resolution_note}`}
        </p>
      )}
      {query.data?.status !== "open" &&
        (!item.appeal_id || query.isSuccess) && (
          <Button variant="outline" onClick={() => setOpen(!open)}>
            申诉
          </Button>
        )}
      {open && (
        <form
          className="space-y-2"
          onSubmit={(event) => {
            event.preventDefault()
            create.mutate()
          }}
        >
          <label className="block" htmlFor={`appeal-${item.id}`}>
            申诉原因
            <textarea
              className="w-full rounded-md border bg-background p-3"
              id={`appeal-${item.id}`}
              aria-label="申诉原因"
              value={reason}
              maxLength={4000}
              onChange={(event) => setReason(event.target.value)}
            />
          </label>
          <Button disabled={!reason.trim() || create.isPending} type="submit">
            提交申诉
          </Button>
        </form>
      )}
    </div>
  )
}
