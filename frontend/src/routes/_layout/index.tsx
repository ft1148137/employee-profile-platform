import { createFileRoute, Navigate } from "@tanstack/react-router"
import { OrgState } from "@/components/Profile/OrgState"
import { SetupError } from "@/components/Setup/SetupError"
import { useAdminConversations } from "@/components/Sidebar/AdminConversations"
import { useInbox } from "@/hooks/useInbox"
import { useOrg } from "@/hooks/useOrg"
import { useSetup } from "@/hooks/useSetup"

export const Route = createFileRoute("/_layout/")({ component: Home })

function Home() {
  const { org, roles, employeeId } = useOrg()
  const inbox = useInbox()
  const setup = useSetup()
  const history = useAdminConversations(
    roles.includes("org_admin") ? org?.id : undefined,
  )
  if (!org) return <OrgState />
  if (roles.includes("org_admin")) {
    if (setup.isPending || history.isPending)
      return <p role="status">正在加载对话…</p>
    if (setup.isError || history.isError)
      return <SetupError error={setup.error ?? history.error} />
    const session = !setup.data.completed
      ? history.data?.data.find((entry) => entry.purpose === "setup")?.id
      : undefined
    return <Navigate to="/admin/assistant" search={{ session }} replace />
  }
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
