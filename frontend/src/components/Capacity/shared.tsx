import { useQuery } from "@tanstack/react-query"
import { CapacityService, type WorkloadPublic } from "@/client"
import { Badge } from "@/components/ui/badge"

export function useCapacitySettings(orgId?: string) {
  return useQuery({
    queryKey: ["capacity-settings", orgId],
    queryFn: async () =>
      (await CapacityService.getCapacitySettings({ path: { org_id: orgId! } }))
        .data,
    enabled: !!orgId,
  })
}

const bands = {
  unconfirmed: "未确认",
  available: "有余量",
  full: "基本满",
  overloaded: "超负荷",
} as const

export function WorkloadBand({ band }: Pick<WorkloadPublic, "band">) {
  return (
    <Badge variant={band === "overloaded" ? "destructive" : "secondary"}>
      {bands[band]}
    </Badge>
  )
}

export function WorkloadNumbers({ workload }: { workload: WorkloadPublic }) {
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <div className="rounded-xl border p-5">
        <p className="text-sm text-muted-foreground">Jira 参考值</p>
        <p className="mt-2 font-medium">
          {workload.jira_task_count === null
            ? "无，未接 Jira 或未关联 Jira 账号"
            : `${workload.jira_task_count} 件未完成`}
        </p>
      </div>
      <div className="rounded-xl border bg-muted/30 p-5">
        <p className="text-sm text-muted-foreground">经理确认</p>
        <p className="my-2 text-3xl font-semibold tabular-nums">
          {workload.confirmed_percent === null
            ? "未确认"
            : `${workload.confirmed_percent}%`}
        </p>
        <WorkloadBand band={workload.band} />
      </div>
    </div>
  )
}

export const workloadNotice =
  "Jira 参考值只是参考，不代表实际工作量；负荷不是绩效，不打分、不排名。"
