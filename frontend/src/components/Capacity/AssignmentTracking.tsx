import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useId, useState } from "react"
import { CapacityTrackingService, type TrackingState } from "@/client"
import { ProfileError } from "@/components/Profile/QueryState"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { CapacityError, useCapacityRefresh } from "./AssignmentFields"

export const trackingLabels: Record<TrackingState, string> = {
  status_unknown: "有关联任务状态待确认",
  no_tasks: "未关联任务",
  on_track: "任务进展正常",
  tasks_done: "关联任务已全部完成，可以结束分配",
  overdue: "任务逾期",
  handed_over: "任务已交接",
}

export function AssignmentTracking({
  orgId,
  assignmentId,
}: {
  orgId: string
  assignmentId: string
}) {
  const cache = useQueryClient()
  const refresh = useCapacityRefresh(orgId)
  const inputId = useId()
  const [query, setQuery] = useState("")
  const path = { org_id: orgId, assignment_id: assignmentId }
  const key = ["capacity", orgId, "tracking", assignmentId]
  const tracking = useQuery({
    queryKey: key,
    queryFn: async () =>
      (await CapacityTrackingService.trackingGetAssignmentTracking({ path }))
        .data,
  })
  const search = useMutation({
    mutationFn: async (q: string) =>
      (
        await CapacityTrackingService.trackingSearchAssignmentTasks({
          path,
          query: { q },
        })
      ).data,
  })
  const change = useMutation({
    mutationFn: async ({ taskId, link }: { taskId: string; link: boolean }) =>
      link
        ? CapacityTrackingService.trackingLinkAssignmentTask({
            path,
            body: { task_id: taskId },
          })
        : CapacityTrackingService.trackingUnlinkAssignmentTask({
            path: { ...path, task_id: taskId },
          }),
    onSuccess: (result) => {
      cache.setQueryData(key, result.data)
      refresh()
    },
  })
  if (tracking.isPending)
    return <p className="text-sm text-muted-foreground">正在加载任务跟踪…</p>
  if (tracking.isError) return <ProfileError retry={() => tracking.refetch()} />
  const tasks = tracking.data.tasks
  const results = search.data?.filter((candidate) =>
    tasks.every((task) => task.id !== candidate.id),
  )
  return (
    <section aria-label="任务跟踪" className="space-y-2 border-t pt-3 text-sm">
      <p aria-live="polite">{trackingLabels[tracking.data.tracking_state]}</p>
      {tasks.length > 0 && (
        <ul className="space-y-1" aria-label="已关联任务">
          {tasks.map((task) => (
            <li className="flex flex-wrap items-center gap-2" key={task.id}>
              <span>
                {task.external_key} · {task.title}
              </span>
              <Button
                variant="ghost"
                size="sm"
                disabled={change.isPending}
                onClick={() => change.mutate({ taskId: task.id, link: false })}
              >
                取消关联
              </Button>
            </li>
          ))}
        </ul>
      )}
      <form
        className="space-y-1"
        onSubmit={(event) => {
          event.preventDefault()
          if (query.trim()) search.mutate(query.trim())
        }}
      >
        <label htmlFor={inputId}>任务编号或标题</label>
        <div className="flex items-center gap-2">
          <Input
            id={inputId}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            maxLength={100}
          />
          <Button
            type="submit"
            variant="outline"
            disabled={!query.trim() || search.isPending}
          >
            搜索任务
          </Button>
        </div>
      </form>
      {search.isSuccess &&
        (results?.length ? (
          <ul className="space-y-1" aria-label="可关联任务">
            {results.map((task) => (
              <li className="flex flex-wrap items-center gap-2" key={task.id}>
                <span>
                  {task.external_key} · {task.title}（{task.source}）
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={change.isPending}
                  onClick={() => change.mutate({ taskId: task.id, link: true })}
                >
                  关联任务
                </Button>
              </li>
            ))}
          </ul>
        ) : (
          <p>没有可关联的匹配任务。</p>
        ))}
      <CapacityError error={search.error ?? change.error} />
    </section>
  )
}
