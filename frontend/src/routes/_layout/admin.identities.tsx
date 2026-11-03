import { createFileRoute } from "@tanstack/react-router"
import { IdentityTable } from "@/components/Identities/IdentityTable"
import { OrgState } from "@/components/Profile/OrgState"
import { useOrg } from "@/hooks/useOrg"

export const Route = createFileRoute("/_layout/admin/identities")({
  component: IdentitiesPage,
  head: () => ({ meta: [{ title: "账号对应 - 员工画像平台" }] }),
})

function IdentitiesPage() {
  const { org, roles } = useOrg()
  if (!org) return <OrgState />
  if (!roles.includes("org_admin"))
    return <p role="alert">需要企业管理员权限</p>
  return (
    <div className="space-y-6">
      <header className="border-l-4 border-primary pl-4">
        <p className="mb-1 text-xs tracking-widest text-muted-foreground">
          管理 · 外部账号
        </p>
        <h1 className="text-3xl font-semibold tracking-tight">账号对应</h1>
      </header>
      <IdentityTable key={org.id} orgId={org.id} />
    </div>
  )
}
