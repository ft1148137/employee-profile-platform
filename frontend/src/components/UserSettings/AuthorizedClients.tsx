import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { OauthService } from "@/client"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
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
    <section className="space-y-3" aria-label="已授权的客户端">
      <h3 className="font-semibold">已授权的客户端</h3>
      <p className="text-sm text-muted-foreground">
        名称由客户端自己填写。撤销后，该客户端需要重新获得你的授权。
      </p>
      {grants.isPending ? (
        <ProfileLoading />
      ) : grants.isError ? (
        <ProfileError retry={() => grants.refetch()} />
      ) : grants.data.length === 0 ? (
        <p>还没有授权客户端</p>
      ) : (
        grants.data.map((grant) => (
          <fieldset
            key={grant.id}
            aria-label={grant.client_name}
            className="flex flex-wrap items-center justify-between gap-3 rounded-lg border p-4"
          >
            <div>
              <p className="font-medium">{grant.client_name}</p>
              <p className="text-xs text-muted-foreground">
                授权时间：{new Date(grant.created_at).toLocaleString("zh-CN")} ·
                最近使用：
                {grant.last_used_at
                  ? new Date(grant.last_used_at).toLocaleString("zh-CN")
                  : "尚未使用"}
              </p>
            </div>
            <Button
              variant="outline"
              disabled={revoke.isPending}
              onClick={() => revoke.mutate(grant.id)}
            >
              撤销
            </Button>
          </fieldset>
        ))
      )}
      {message && <p role="status">{message}</p>}
    </section>
  )
}
