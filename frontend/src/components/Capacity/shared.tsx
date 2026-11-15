import { useQuery, useQueryClient } from "@tanstack/react-query"
import { AxiosError } from "axios"
import { useEffect } from "react"
import { CapacityService, type WorkloadPublic } from "@/client"
import { client } from "@/client/client.gen"
import { Badge } from "@/components/ui/badge"

export function useCapacitySettings(orgId?: string) {
  return useQuery({
    queryKey: ["capacity-settings", orgId],
    queryFn: async () =>
      (await CapacityService.getCapacitySettings({ path: { org_id: orgId! } }))
        .data,
    enabled: !!orgId,
    refetchOnWindowFocus: true,
    staleTime: 0,
  })
}

export function useCapacityDisabledHandling(orgId?: string) {
  const queryClient = useQueryClient()
  useEffect(() => {
    if (!orgId) return
    const interceptor = client.instance.interceptors.response.use(
      undefined,
      async (error: unknown) => {
        if (
          error instanceof AxiosError &&
          error.response?.status === 404 &&
          error.response.data?.detail === "需求与容量模块未开启" &&
          new URL(
            error.config?.url ?? "",
            window.location.origin,
          ).pathname.startsWith(`/api/v1/orgs/${orgId}/capacity/`)
        ) {
          await queryClient.invalidateQueries({
            queryKey: ["capacity-settings", orgId],
          })
        }
        return Promise.reject(error)
      },
    )
    return () => client.instance.interceptors.response.eject(interceptor)
  }, [orgId, queryClient])
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
