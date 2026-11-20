import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { OauthService } from "@/client"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { handleError } from "@/utils"

export function AuthorizedClients({ orgId }: { orgId: string }) {
  const client = useQueryClient()
  const [message, setMessage] = useState("")
  const queryKey = ["oauth-grants", orgId]
  const grants = useQuery({
    queryKey,
    queryFn: async () =>
      (await OauthService.readGrants({ query: { org_id: orgId } })).data,
  })
  const revoke = useMutation({
    mutationFn: (id: string) =>
      OauthService.revokeAuthorization({
        query: { org_id: orgId },
        path: { grant_id: id },
      }),
    onSuccess: () => {
      setMessage("授权已撤销")
      client.invalidateQueries({ queryKey })
    },
    onError: handleError.bind(setMessage),
  })
  return (
    <section className="divide-y" aria-label="已授权应用">
      <div className="px-4 py-2.5">
        <h3 className="font-medium">已授权应用</h3>
        <p className="text-xs text-muted-foreground">
          撤销后应用需重新授权才能访问。
        </p>
      </div>
      {grants.isPending ? (
        <ProfileLoading />
      ) : grants.isError ? (
        <ProfileError retry={() => grants.refetch()} />
      ) : grants.data.length === 0 ? (
        <p className="px-4 py-2.5 text-muted-foreground">暂无已授权应用</p>
      ) : (
        grants.data.map((grant) => (
          <fieldset
            key={grant.id}
            aria-label={grant.client_name}
            className="flex min-w-0 flex-wrap items-center justify-between gap-x-4 gap-y-1 px-4 py-2.5"
          >
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <p className="min-w-0 wrap-anywhere font-medium">
                  {grant.client_name}
                </p>
                <Badge
                  variant="outline"
                  className="border-primary/20 bg-primary/5 text-primary"
                >
                  已授权
                </Badge>
              </div>
              <p className="text-xs text-muted-foreground">
                授权时间：{new Date(grant.created_at).toLocaleString("zh-CN")} ·
                最近使用：
                {grant.last_used_at
                  ? new Date(grant.last_used_at).toLocaleString("zh-CN")
                  : "尚未使用"}
              </p>
            </div>
            <Button
              size="sm"
              variant="outline"
              className="ml-auto"
              disabled={revoke.isPending}
              onClick={() => revoke.mutate(grant.id)}
            >
              撤销
            </Button>
          </fieldset>
        ))
      )}
      {message && (
        <p role="status" className="px-4 py-2.5">
          {message}
        </p>
      )}
    </section>
  )
}
