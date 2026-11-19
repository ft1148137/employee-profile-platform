import { createFileRoute, Navigate } from "@tanstack/react-router"
import { OrgState } from "@/components/Profile/OrgState"
import { useOrg } from "@/hooks/useOrg"

export const Route = createFileRoute("/_layout/find-people")({
  component: FindPeoplePage,
  head: () => ({ meta: [{ title: "找人 - 员工画像平台" }] }),
})

function FindPeoplePage() {
  const { org, roles } = useOrg()
  if (!org) return <OrgState />
  const isAdmin = roles.includes("org_admin")
  if (!isAdmin && !roles.includes("dept_manager"))
    return <p role="alert">没有权限使用找人，需要企业管理员或部门经理权限</p>
  return (
    <Navigate
      to="/assistant"
      search={{ mode: isAdmin ? "admin" : "personal", find: true }}
      replace
    />
  )
}
