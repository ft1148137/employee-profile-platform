import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import { OrgsService } from "@/client"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useOrg } from "@/hooks/useOrg"
import { SetupError } from "./SetupError"

export function OrgProfileStep({
  orgId,
  onComplete,
}: {
  orgId: string
  onComplete: () => void
}) {
  const { org } = useOrg()
  const client = useQueryClient()
  const mutation = useMutation({
    mutationFn: (name: string) =>
      OrgsService.renameOrganization({
        path: { org_id: orgId },
        body: { name },
      }),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ["memberships"] })
      onComplete()
    },
  })
  return (
    <form
      id="org-profile"
      className="space-y-5"
      aria-busy={mutation.isPending}
      onSubmit={(event) => {
        event.preventDefault()
        if (!mutation.isPending)
          mutation.mutate(
            String(new FormData(event.currentTarget).get("name")).trim(),
          )
      }}
    >
      <div className="space-y-2">
        <Label htmlFor="org-name">企业名称</Label>
        <Input
          key={org?.name}
          id="org-name"
          name="name"
          defaultValue={org?.name}
          required
          maxLength={100}
          disabled={mutation.isPending}
        />
      </div>
      <p className="text-sm text-muted-foreground">
        管理员账号已创建，可随时在
        <Link to="/settings" className="text-primary underline">
          「设置 → 账号与安全」
        </Link>
        修改密码。
      </p>
      <SetupError error={mutation.error} />
    </form>
  )
}
