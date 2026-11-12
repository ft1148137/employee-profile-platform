import { createFileRoute, Navigate } from "@tanstack/react-router"
import { OrgState } from "@/components/Profile/OrgState"
import { useInbox } from "@/hooks/useInbox"
import { useOrg } from "@/hooks/useOrg"

export const Route = createFileRoute("/_layout/")({ component: Home })

function Home() {
  const { org, roles, employeeId } = useOrg()
  const inbox = useInbox()
  if (!org) return <OrgState />
  if (roles.includes("org_admin"))
    return (
      <Navigate to="/admin/assistant" search={{ prompt: undefined }} replace />
    )
  if (roles.includes("dept_manager"))
    return <Navigate to="/employees" replace />
  if (employeeId) {
    if (inbox.isPending) return <p role="status">正在加载收件箱…</p>
    if (inbox.isError)
      return <div role="alert">收件箱加载失败，请刷新重试。</div>
    if (inbox.data.pending_count > 0) return <Navigate to="/inbox" replace />
    return (
      <Navigate to="/employees/$employeeId" params={{ employeeId }} replace />
    )
  }
  return <p>当前组织尚未为你分配员工身份或管理角色，请联系管理员。</p>
}
