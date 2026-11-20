import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { ReviewDelegationsService } from "@/client"
import { SectionCard } from "@/components/Common/WorkbenchPrimitives"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { SetupError } from "@/components/Setup/SetupError"
import { Button } from "@/components/ui/button"
import { useOrg } from "@/hooks/useOrg"

export function ReviewDelegation() {
  const { org, managedDepartmentIds } = useOrg()
  return org ? (
    <Delegations
      key={org.id}
      orgId={org.id}
      manager={managedDepartmentIds.length > 0}
    />
  ) : null
}

function Delegations({ orgId, manager }: { orgId: string; manager: boolean }) {
  const cache = useQueryClient()
  const path = { org_id: orgId }
  const queryKey = ["review-delegations", orgId]
  const [delegate, setDelegate] = useState("")
  const [starts, setStarts] = useState("")
  const [ends, setEnds] = useState("")
  const query = useQuery({
    queryKey,
    queryFn: async () =>
      (await ReviewDelegationsService.delegationsReadDelegations({ path }))
        .data,
  })
  const refresh = () => cache.invalidateQueries({ queryKey })
  const create = useMutation({
    mutationFn: () =>
      ReviewDelegationsService.delegationsCreateDelegation({
        path,
        body: {
          delegate_user_id: delegate,
          starts_at: new Date(starts).toISOString(),
          ends_at: new Date(ends).toISOString(),
        },
      }),
    onSuccess: refresh,
  })
  const revoke = useMutation({
    mutationFn: (id: string) =>
      ReviewDelegationsService.delegationsRevokeDelegation({
        path: { ...path, delegation_id: id },
      }),
    onSuccess: refresh,
  })
  if (query.isPending) return <ProfileLoading />
  if (query.isError) return <ProfileError retry={query.refetch} />
  return (
    <SectionCard aria-label="代理审批" className="space-y-3">
      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.5fr)]">
        <header>
          <h2 className="text-sm font-semibold">代理审批</h2>
          <p className="text-sm text-muted-foreground">
            代理期间你本人仍可审批；代理人仅可代审你的待办，不可自审或转交。
          </p>
        </header>
        {manager ? (
          <form
            className="min-w-0 space-y-2 text-sm"
            onSubmit={(event) => {
              event.preventDefault()
              create.mutate()
            }}
          >
            <label className="block min-w-0 space-y-1">
              <span>代理人</span>
              <select
                aria-label="代理人"
                className="w-full rounded-md border bg-background p-2"
                required
                value={delegate}
                onChange={(event) => setDelegate(event.target.value)}
              >
                <option value="" disabled>
                  请选择企业成员
                </option>
                {query.data.candidates.map((candidate) => (
                  <option key={candidate.user_id} value={candidate.user_id}>
                    {candidate.name}
                  </option>
                ))}
              </select>
            </label>
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-1 xl:grid-cols-2">
              <label className="block min-w-0 space-y-1">
                <span>开始时间</span>
                <input
                  className="w-full min-w-0 rounded-md border bg-background p-2"
                  required
                  type="datetime-local"
                  value={starts}
                  onChange={(event) => setStarts(event.target.value)}
                />
              </label>
              <label className="block min-w-0 space-y-1">
                <span>结束时间</span>
                <input
                  className="w-full min-w-0 rounded-md border bg-background p-2"
                  required
                  type="datetime-local"
                  min={starts}
                  value={ends}
                  onChange={(event) => setEnds(event.target.value)}
                />
              </label>
            </div>
            <div className="flex justify-end">
              <Button size="sm" disabled={create.isPending || revoke.isPending}>
                保存代理
              </Button>
            </div>
          </form>
        ) : (
          <p className="text-sm text-muted-foreground lg:text-right">
            只有部门经理可以设置代理审批人。
          </p>
        )}
      </div>
      <SetupError error={create.error || revoke.error} />
      <section className="divide-y border-t" aria-label="代理记录">
        {query.data.data.length === 0 && (
          <p className="pt-3 text-sm text-muted-foreground">暂无代理记录</p>
        )}
        {query.data.data.map((row) => (
          <div
            key={row.id}
            className="flex flex-wrap items-center justify-between gap-2 py-2.5 text-sm"
          >
            <div className="min-w-0 wrap-anywhere">
              <p className="font-medium">{row.delegate_name}</p>
              <p className="text-sm text-muted-foreground">
                {new Date(row.starts_at).toLocaleString("zh-CN")} 至{" "}
                {new Date(row.ends_at).toLocaleString("zh-CN")}
              </p>
              <p className="text-sm">
                {row.revoked_at
                  ? "已撤销"
                  : new Date(row.ends_at) <= new Date()
                    ? "已到期"
                    : new Date(row.starts_at) > new Date()
                      ? "尚未开始"
                      : "代理中"}
              </p>
            </div>
            {!row.revoked_at && (
              <Button
                variant="outline"
                size="sm"
                className="ml-auto"
                disabled={revoke.isPending || create.isPending}
                onClick={() => revoke.mutate(row.id)}
              >
                撤销代理
              </Button>
            )}
          </div>
        ))}
      </section>
    </SectionCard>
  )
}
