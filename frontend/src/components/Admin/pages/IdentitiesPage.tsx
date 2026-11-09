import { IdentityTable } from "@/components/Identities/IdentityTable"
import { OrgState } from "@/components/Profile/OrgState"
import { useOrg } from "@/hooks/useOrg"

export function IdentitiesPage() {
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
        <h1 className="text-3xl font-semibold tracking-tight">Jira 账号匹配</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          邮箱与本企业唯一在职员工一致时自动关联。解除后不会再次自动关联，仍可手动选择员工。
        </p>
      </header>
      <IdentityTable key={org.id} orgId={org.id} />
    </div>
  )
}
