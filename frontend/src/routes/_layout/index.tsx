import { createFileRoute, Navigate, useLocation } from "@tanstack/react-router"
import { OrgState } from "@/components/Profile/OrgState"
import { SetupError } from "@/components/Setup/SetupError"
import { useAdminConversations } from "@/components/Sidebar/AdminConversations"
import { useOrg } from "@/hooks/useOrg"
import { useSetup } from "@/hooks/useSetup"

export const Route = createFileRoute("/_layout/")({ component: Home })

function Home() {
  const pathname = useLocation({ select: (location) => location.pathname })
  const { org, roles, employeeId } = useOrg()
  const setup = useSetup()
  const history = useAdminConversations(
    roles.includes("org_admin") ? org?.id : undefined,
  )
  // This route can still render while the next page is loading.
  if (pathname !== "/") return null
  if (!org) return <OrgState />
  if (roles.includes("org_admin")) {
    if (setup.isPending || history.isPending)
      return <p role="status">加载中…</p>
    if (setup.isError || history.isError)
      return <SetupError error={setup.error ?? history.error} />
    const session = !setup.data.completed
      ? history.data?.data.find((entry) => entry.purpose === "setup")?.id
      : undefined
    return (
      <Navigate to="/assistant" search={{ mode: "admin", session }} replace />
    )
  }
  if (employeeId) return <Navigate to="/assistant" replace />
  if (roles.includes("dept_manager"))
    return <Navigate to="/employees" replace />
  return <p>你的账号尚未绑定员工身份或管理权限，请联系企业管理员处理。</p>
}
