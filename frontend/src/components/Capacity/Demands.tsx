import { useQuery } from "@tanstack/react-query"
import { useState } from "react"
import { CapacityService } from "@/client"
import { dateLabel } from "@/components/Profile/fields"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { Button } from "@/components/ui/button"
import { DemandsCreate } from "./DemandsCreate"
import { DemandsDetail, demandStatuses } from "./DemandsDetail"

export function OutgoingDemands({ orgId }: { orgId: string }) {
  return <Demands orgId={orgId} side="outgoing" />
}

export function IncomingDemands({ orgId }: { orgId: string }) {
  return <Demands orgId={orgId} side="incoming" />
}

function Demands({
  orgId,
  side,
}: {
  orgId: string
  side: "outgoing" | "incoming"
}) {
  const [selected, setSelected] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const query = useQuery({
    queryKey: ["capacity", orgId, "demands", side],
    queryFn: async () =>
      (
        await CapacityService.listDemands({
          path: { org_id: orgId },
          query: { side },
        })
      ).data,
  })
  if (selected)
    return (
      <DemandsDetail
        key={selected}
        orgId={orgId}
        demandId={selected}
        side={side}
        back={() => setSelected(null)}
      />
    )
  if (creating)
    return (
      <DemandsCreate
        orgId={orgId}
        back={() => setCreating(false)}
        created={(id) => {
          setCreating(false)
          setSelected(id)
        }}
      />
    )
  return (
    <section aria-label="需求列表" className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-semibold">
          {side === "outgoing" ? "我提的需求" : "收到的需求"}
        </h2>
        {side === "outgoing" && (
          <Button onClick={() => setCreating(true)}>问一问 / 提需求</Button>
        )}
      </div>
      {query.isPending ? (
        <ProfileLoading />
      ) : query.isError ? (
        <ProfileError retry={() => query.refetch()} />
      ) : (
        <>
          {!query.data.count && (
            <p className="rounded-xl border border-dashed p-8 text-center text-muted-foreground">
              暂无需求。
            </p>
          )}
          {query.data.data.map((row) => (
            <article key={row.id} className="space-y-2 rounded-xl border p-5">
              <p className="font-medium">
                {row.capability_name} · {demandStatuses[row.status]}
              </p>
              <p className="whitespace-pre-wrap break-words">
                {row.description}
              </p>
              <p className="text-sm text-muted-foreground">
                {row.headcount} 人 · 每人 {row.allocation_percent}% ·{" "}
                {row.window_start} 至 {row.window_end}
              </p>
              <p className="text-xs text-muted-foreground">
                提交于 {dateLabel(row.created_at)}
              </p>
              <Button variant="outline" onClick={() => setSelected(row.id)}>
                查看详情
              </Button>
            </article>
          ))}
        </>
      )}
    </section>
  )
}
