import { useQuery, useQueryClient } from "@tanstack/react-query"
import { AxiosError } from "axios"
import { useEffect, useState } from "react"
import { CapacityService, type WeekLoad, type WorkloadPublic } from "@/client"
import { Badge } from "@/components/ui/badge"

export function isCapacityAccessDenied(error: unknown) {
  return (
    error instanceof AxiosError &&
    [403, 404].includes(error.response?.status ?? 0)
  )
}

export function useCapacitySettings(orgId?: string) {
  return useQuery({
    queryKey: ["capacity-settings", orgId],
    queryFn: async () =>
      (await CapacityService.getCapacitySettings({ path: { org_id: orgId! } }))
        .data,
    enabled: !!orgId,
    refetchOnWindowFocus: true,
  })
}

export function isCapacityDisabled(error: unknown, orgId: string) {
  return (
    error instanceof AxiosError &&
    error.response?.status === 404 &&
    error.response.data?.detail === "规划与负荷功能未开启" &&
    !!error.config?.url &&
    new URL(error.config.url, window.location.origin).pathname.startsWith(
      `/api/v1/orgs/${orgId}/capacity/`,
    )
  )
}

export function useCapacityDisabled(orgId?: string) {
  const client = useQueryClient()
  const [disabledOrgId, setDisabledOrgId] = useState<string>()
  useEffect(() => {
    setDisabledOrgId(undefined)
    if (!orgId) return
    const handleError = (error: unknown) => {
      if (!isCapacityDisabled(error, orgId)) return
      setDisabledOrgId(orgId)
      client.invalidateQueries({ queryKey: ["capacity-settings", orgId] })
    }
    const unsubscribeQueries = client.getQueryCache().subscribe((event) => {
      if (
        event.type === "updated" &&
        event.action.type === "success" &&
        event.query.queryKey[0] === "capacity-settings" &&
        event.query.queryKey[1] === orgId
      )
        setDisabledOrgId(undefined)
      if (
        event.type === "updated" &&
        (event.action.type === "error" || event.action.type === "failed")
      )
        handleError(event.action.error)
    })
    const unsubscribeMutations = client
      .getMutationCache()
      .subscribe((event) => {
        if (event.type === "updated" && event.action.type === "error")
          handleError(event.action.error)
      })
    return () => {
      unsubscribeQueries()
      unsubscribeMutations()
    }
  }, [client, orgId])
  return !!orgId && disabledOrgId === orgId
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
            ? "暂无（未关联 Jira）"
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

export const workloadNotice = "负荷数据仅供参考，不用于绩效考核或排名。"

export function weekLabel(start: string) {
  return `${start.slice(5).replace("-", "/")} 周`
}

export function addWeeks(start: string, weeks: number) {
  const date = new Date(`${start}T00:00:00Z`)
  date.setUTCDate(date.getUTCDate() + weeks * 7)
  return date.toISOString().slice(0, 10)
}

export function weekLoadLabel(week: WeekLoad) {
  if (week.status === "unconfirmed") return "未确认"
  if (week.status === "stale") return "需重新确认"
  return `${(week.other_percent ?? 0) + week.platform_percent}% / ${week.capacity_percent}%`
}
