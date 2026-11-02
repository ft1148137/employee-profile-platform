import { createFileRoute, Navigate } from "@tanstack/react-router"
import { OrgState } from "@/components/Profile/OrgState"
import { useOrg } from "@/hooks/useOrg"

export const Route = createFileRoute("/_layout/")({ component: Home })

function Home() {
  const { org, roles, employeeId } = useOrg()
  if (!org) return <OrgState />
  if (roles.includes("org_admin") || roles.includes("dept_manager"))
    return <Navigate to="/employees" replace />
  if (employeeId)
    return (
      <Navigate to="/employees/$employeeId" params={{ employeeId }} replace />
    )
  return <p>当前组织尚未为你分配员工身份或管理角色，请联系管理员。</p>
}
