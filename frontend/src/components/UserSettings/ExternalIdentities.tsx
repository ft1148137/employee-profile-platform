import { useQuery } from "@tanstack/react-query"
import { useState } from "react"
import { EmployeeRequestsService } from "@/client"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { Button } from "@/components/ui/button"

export function ExternalIdentities({ orgId }: { orgId: string }) {
  const [skip, setSkip] = useState(0)
  const query = useQuery({
    queryKey: ["my-external-identities", orgId, skip],
    queryFn: async () =>
      (
        await EmployeeRequestsService.readMyExternalIdentities({
          path: { org_id: orgId },
          query: { skip, limit: 50 },
        })
      ).data,
  })
  if (query.isPending) return <ProfileLoading />
  if (query.isError) return <ProfileError retry={() => query.refetch()} />
  return (
    <section aria-label="本人关联账号" className="space-y-4 py-4">
      <h2 className="text-lg font-semibold">Jira 账号</h2>
      {!query.data.count && <p>暂无已关联的外部账号</p>}
      {query.data.data.map((identity) => (
        <div key={identity.id} className="rounded-lg border p-4">
          <p>
            {identity.source_label} · {identity.external_id}
          </p>
          <p className="text-sm text-muted-foreground">
            {identity.display_name} {identity.email}
          </p>
        </div>
      ))}
      {(skip > 0 || skip + 50 < query.data.count) && (
        <div className="flex gap-3">
          <Button
            variant="outline"
            disabled={skip === 0}
            onClick={() => setSkip(skip - 50)}
          >
            上一页
          </Button>
          <Button
            variant="outline"
            disabled={skip + 50 >= query.data.count}
            onClick={() => setSkip(skip + 50)}
          >
            下一页
          </Button>
        </div>
      )}
    </section>
  )
}
