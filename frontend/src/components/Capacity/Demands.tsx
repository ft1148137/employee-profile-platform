import { useQuery } from "@tanstack/react-query"
import { Link, useNavigate } from "@tanstack/react-router"
import { useState } from "react"
import {
  CapacityService,
  CapacityTrackingService,
  type DemandPublic,
} from "@/client"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { DemandsDetail, demandStatuses } from "./DemandsDetail"
import { PlanningBoard, type PlanningView } from "./PlanningWorkbench"

const pageSize = 50
const demandColumns = [
  ["submitted", "border-t-blue-500"],
  ["replied", "border-t-amber-500"],
  ["accepted", "border-t-emerald-500"],
  ["withdrawn", "border-t-slate-500"],
] as const

export function DemandList({
  orgId,
  view,
  canRequest,
}: {
  orgId: string
  view: PlanningView
  canRequest: boolean
}) {
  const [skip, setSkip] = useState(0)
  const path = { org_id: orgId }
  const demands = useQuery({
    queryKey: ["capacity", orgId, "outgoing-demands", skip],
    queryFn: async () =>
      (
        await CapacityService.listDemands({
          path,
          query: { side: "outgoing", skip, limit: pageSize },
        })
      ).data,
  })
  const departments = useQuery({
    queryKey: ["capacity", orgId, "departments"],
    enabled: canRequest,
    queryFn: async () =>
      (await CapacityService.listCapacityDepartments({ path })).data,
    select: (result) => result.data,
  })
  if (demands.isPending) return <ProfileLoading />
  if (demands.isError) return <ProfileError retry={() => demands.refetch()} />
  const render = (demand: DemandPublic) => (
    <article
      key={demand.id}
      className={cn(
        "min-w-0 rounded-md border border-surface-border bg-surface px-3 py-2 text-sm",
        view === "list" &&
          "grid items-center gap-x-4 gap-y-1 md:grid-cols-[minmax(0,2fr)_minmax(0,2fr)_auto]",
      )}
    >
      <div className="min-w-0">
        <span
          className="font-mono text-xs text-muted-foreground"
          title={demand.id}
        >
          {demand.id.slice(-8)}
        </span>
        <h3 className="break-words font-semibold">
          {demand.capability_name}
          {demand.project && ` · ${demand.project.role_name}`}
        </h3>
        <p className="line-clamp-2 break-words text-muted-foreground">
          {demand.description}
        </p>
      </div>
      <div className="mt-1 min-w-0 text-xs text-muted-foreground">
        <p>
          {demand.window_start} 至 {demand.window_end} · {demand.headcount} 人 ·{" "}
          {demand.allocation_percent}%
        </p>
        {
          departments.data?.find(({ id }) => id === demand.target_department_id)
            ?.name
        }
      </div>
      <div className="mt-1 flex flex-wrap items-center justify-between gap-1">
        <Badge variant="secondary">{demandStatuses[demand.status]}</Badge>
        <Button size="sm" variant="ghost" asChild>
          <Link to="/capacity" search={{ tab: "outgoing", demand: demand.id }}>
            查看详情
          </Link>
        </Button>
      </div>
    </article>
  )
  const { data, count } = demands.data
  return (
    <section aria-label="已发出的需求" className="space-y-3">
      {view === "board" ? (
        <PlanningBoard
          groups={demandColumns.map(([status, color]) => ({
            label: demandStatuses[status],
            color,
            cards: data
              .filter((demand) => demand.status === status)
              .map(render),
          }))}
        />
      ) : (
        <div className="space-y-1">
          {data.length ? (
            data.map(render)
          ) : (
            <p className="text-sm text-muted-foreground">暂无发出的需求。</p>
          )}
        </div>
      )}
      {(skip > 0 || count > pageSize) && (
        <div className="flex items-center gap-2 text-sm">
          <Button
            size="sm"
            variant="outline"
            disabled={skip === 0}
            onClick={() => setSkip(skip - pageSize)}
          >
            上一页
          </Button>
          <span>共 {count} 条</span>
          <Button
            size="sm"
            variant="outline"
            disabled={skip + pageSize >= count}
            onClick={() => setSkip(skip + pageSize)}
          >
            下一页
          </Button>
        </div>
      )}
    </section>
  )
}

export function OutgoingDemands({
  orgId,
  demand,
}: {
  orgId: string
  demand: string
}) {
  return <Demands orgId={orgId} side="outgoing" demand={demand} />
}

export function IncomingDemands({
  orgId,
  demand,
  plansOpen,
}: {
  orgId: string
  demand: string
  plansOpen?: boolean
}) {
  return (
    <Demands
      orgId={orgId}
      side="incoming"
      demand={demand}
      plansOpen={plansOpen}
    />
  )
}

function Demands({
  orgId,
  side,
  demand,
  plansOpen,
}: {
  orgId: string
  side: "outgoing" | "incoming"
  demand: string
  plansOpen?: boolean
}) {
  const navigate = useNavigate()
  const risk = useQuery({
    queryKey: ["capacity", orgId, "tracking-risk", demand],
    enabled: side === "outgoing",
    retry: false,
    queryFn: async () =>
      (
        await CapacityTrackingService.trackingGetDemandTrackingRisk({
          path: { org_id: orgId, demand_id: demand },
        })
      ).data,
  })
  return (
    <DemandsDetail
      key={demand}
      orgId={orgId}
      demandId={demand}
      side={side}
      plansOpen={plansOpen}
      riskMessage={risk.data?.message}
      back={() => {
        if (side === "incoming")
          navigate({ to: "/reviews", search: { type: "demand" } })
        else navigate({ to: "/capacity", search: { tab: side } })
      }}
    />
  )
}
