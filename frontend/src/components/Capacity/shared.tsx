import { useQuery, useQueryClient } from "@tanstack/react-query"
import { AxiosError } from "axios"
import { useEffect, useState } from "react"
import { CapacityService, type WeekLoad, type WorkloadPublic } from "@/client"
import { HelpTip } from "@/components/Common/WorkbenchPrimitives"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"

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

export const unavailableStripes =
  "bg-[repeating-linear-gradient(135deg,transparent_0_5px,var(--border)_5px_7px)]"

export const loadColors = {
  unconfirmed: {
    surface: "border-dashed bg-muted/50",
    badge: "border-border bg-muted text-muted-foreground",
    bar: "bg-muted-foreground",
  },
  available: {
    surface: "border-teal-600/25 bg-teal-500/5",
    badge: "border-teal-600/30 bg-teal-500/10 text-teal-800 dark:text-teal-300",
    bar: "bg-teal-600 dark:bg-teal-400",
  },
  full: {
    surface: "border-amber-600/30 bg-amber-500/10",
    badge:
      "border-amber-600/30 bg-amber-500/10 text-amber-800 dark:text-amber-300",
    bar: "bg-amber-500",
  },
  overloaded: {
    surface: "border-red-600/30 bg-red-500/10",
    badge: "border-red-600/30 bg-red-500/10 text-red-700 dark:text-red-300",
    bar: "bg-red-500",
  },
}

export function occupiedPercent(week: WeekLoad) {
  return (week.other_percent ?? 0) + week.platform_percent
}

export function weekBand(week: WeekLoad): WorkloadPublic["band"] {
  if (week.status !== "confirmed") return "unconfirmed"
  const total = occupiedPercent(week)
  if (total > week.capacity_percent) return "overloaded"
  return total < Math.floor(week.capacity_percent * 0.8) ? "available" : "full"
}

export function loadBandLabel(band: WorkloadPublic["band"]) {
  return bands[band]
}

export function UtilizationBadge({
  band,
  percent,
}: {
  band: WorkloadPublic["band"]
  percent: number | null
}) {
  return (
    <Badge
      variant="outline"
      className={cn("shrink-0 tabular-nums", loadColors[band].badge)}
      title={bands[band]}
    >
      {band === "unconfirmed" || percent === null ? bands[band] : `${percent}%`}
    </Badge>
  )
}

export const workloadNotice = "负荷数据仅供参考，不用于绩效考核或排名。"

export function WorkloadHelp() {
  return <HelpTip label="负荷数据说明" content={workloadNotice} />
}

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
  return `${occupiedPercent(week)}% / ${week.capacity_percent}%`
}
