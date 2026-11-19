import { useQuery } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { CapacityTrackingService } from "@/client"
import { DemandsDetail } from "./DemandsDetail"

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
    <div className="space-y-4">
      {risk.data?.message && (
        <p role="status" className="rounded-lg border p-3 text-sm">
          {risk.data.message}
        </p>
      )}
      <DemandsDetail
        key={demand}
        orgId={orgId}
        demandId={demand}
        side={side}
        plansOpen={plansOpen}
        back={() => {
          if (side === "incoming")
            navigate({ to: "/reviews", search: { type: "demand" } })
          else navigate({ to: "/capacity", search: { tab: side } })
        }}
      />
    </div>
  )
}
