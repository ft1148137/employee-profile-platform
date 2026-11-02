import { createFileRoute } from "@tanstack/react-router"
import { OrgState } from "@/components/Profile/OrgState"
import { RosterImport } from "@/components/Roster/RosterImport"
import { useOrg } from "@/hooks/useOrg"

export const Route = createFileRoute("/_layout/admin/roster")({
  component: RosterPage,
  head: () => ({ meta: [{ title: "导入名单 - 员工画像平台" }] }),
})

function RosterPage() {
  const { org, roles } = useOrg()
  if (!org) return <OrgState />
  if (!roles.includes("org_admin"))
    return <p role="alert">需要企业管理员权限</p>
  return (
    <div className="min-w-0 space-y-6">
      <div className="border-l-4 border-primary pl-4">
        <h1 className="text-3xl font-semibold">导入名单</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          上传、核对，再更新企业人员名单。
        </p>
      </div>
      <RosterImport key={org.id} orgId={org.id} />
    </div>
  )
}
