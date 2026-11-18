import { useMutation, useQuery } from "@tanstack/react-query"
import { useState } from "react"
import {
  CapabilitiesService,
  CapacityTrackingService,
  type FeedbackAssignment,
  type FeedbackInput,
} from "@/client"
import { Button } from "@/components/ui/button"
import { CapacityError, useCapacityRefresh } from "./AssignmentFields"

export function useFeedbackAssignments(orgId: string, demandId?: string) {
  return useQuery({
    queryKey: ["capacity", orgId, "feedback-assignments", demandId],
    queryFn: async () =>
      (
        await CapacityTrackingService.trackingListFeedbackAssignments({
          path: { org_id: orgId },
          query: { demand_id: demandId },
        })
      ).data,
  })
}

export function AssignmentFeedback({
  orgId,
  assignment,
}: {
  orgId: string
  assignment: FeedbackAssignment
}) {
  const [open, setOpen] = useState(false)
  const refresh = useCapacityRefresh(orgId)
  const dictionary = useQuery({
    queryKey: ["assignment-feedback-capabilities", orgId],
    queryFn: async () =>
      (await CapabilitiesService.readCapabilities({ path: { org_id: orgId } }))
        .data.data,
    enabled: open,
  })
  const submit = useMutation({
    mutationFn: (body: FeedbackInput) =>
      CapacityTrackingService.trackingCreateAssignmentFeedback({
        path: { org_id: orgId, assignment_id: assignment.id },
        body,
      }),
    onSuccess: refresh,
  })
  if (assignment.has_feedback || submit.isSuccess) return <p>已写反馈</p>
  return open ? (
    <form
      aria-label="写工作反馈"
      className="space-y-3"
      onSubmit={(event) => {
        event.preventDefault()
        const data = new FormData(event.currentTarget)
        submit.mutate({
          text: String(data.get("feedback")),
          capability_keys: data.getAll("capability").map(String),
        })
      }}
    >
      <fieldset className="space-y-3" disabled={submit.isPending}>
        <label className="block space-y-1">
          工作反馈（20～500 字）
          <textarea
            name="feedback"
            className="block min-h-24 w-full rounded-md border bg-background p-2"
            required
            minLength={20}
            maxLength={500}
          />
        </label>
        <fieldset className="grid gap-2 sm:grid-cols-2">
          <legend>相关能力</legend>
          {dictionary.data?.map(({ key, name }) => (
            <label key={key} className="flex items-center gap-2">
              <input
                name="capability"
                type="checkbox"
                value={key}
                defaultChecked={assignment.default_keys.includes(key)}
              />
              {name}
            </label>
          ))}
        </fieldset>
        <CapacityError error={dictionary.error || submit.error} />
        <div className="flex gap-2">
          <Button type="submit" disabled={!dictionary.isSuccess}>
            发送给员工过目
          </Button>
          <Button
            type="button"
            variant="outline"
            onClick={() => setOpen(false)}
          >
            取消
          </Button>
        </div>
      </fieldset>
    </form>
  ) : (
    <Button variant="outline" onClick={() => setOpen(true)}>
      写反馈
    </Button>
  )
}
