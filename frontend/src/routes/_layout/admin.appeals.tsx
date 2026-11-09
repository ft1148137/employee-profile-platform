import { createFileRoute } from "@tanstack/react-router"
import { Disputes } from "@/components/Admin/Disputes"
import { OrgState } from "@/components/Profile/OrgState"
import { useOrg } from "@/hooks/useOrg"

export const Route = createFileRoute("/_layout/admin/appeals")({
  component: Page,
  head: () => ({ meta: [{ title: "争议 - 员工画像平台" }] }),
})
function Page() {
  const { org, roles } = useOrg()
  if (!org) return <OrgState />
  if (!roles.includes("org_admin"))
    return <p role="alert">需要企业管理员权限</p>
  return <Disputes key={org.id} orgId={org.id} />
}
