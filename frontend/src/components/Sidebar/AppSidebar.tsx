import { useLocation, useNavigate } from "@tanstack/react-router"
import {
  Inbox,
  ListChecks,
  ScrollText,
  Settings,
  UserRound,
  Users,
} from "lucide-react"
import { SidebarAppearance } from "@/components/Common/Appearance"
import { Logo } from "@/components/Common/Logo"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarHeader,
} from "@/components/ui/sidebar"
import useAuth from "@/hooks/useAuth"
import { useInbox } from "@/hooks/useInbox"
import { useOrg } from "@/hooks/useOrg"
import { useReviews } from "@/hooks/useReviews"
import { useSetup } from "@/hooks/useSetup"
import { type Item, Main } from "./Main"
import { User } from "./User"

export function AppSidebar() {
  const { user: currentUser } = useAuth()

  const { org, memberships, roles, employeeId, setOrg } = useOrg()
  const setup = useSetup()
  const inbox = useInbox()
  const reviews = useReviews()
  const navigate = useNavigate()
  const location = useLocation()
  const items: Item[] = []
  if (org)
    items.push({
      icon: Inbox,
      title: "收件箱",
      path: "/inbox",
      badge: inbox.data?.unread_count
        ? String(inbox.data.unread_count)
        : undefined,
    })
  if (reviews.data?.count)
    items.push({
      icon: ListChecks,
      title: "待审批",
      path: "/reviews",
      badge: String(reviews.data.count),
    })
  if (employeeId)
    items.push({
      icon: UserRound,
      title: "我的画像",
      path: `/employees/${employeeId}`,
    })
  if (roles.includes("org_admin") || roles.includes("dept_manager"))
    items.push({ icon: Users, title: "员工", path: "/employees" })
  items.push({ icon: Settings, title: "设置", path: "/settings" })

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader className="px-4 py-6 group-data-[collapsible=icon]:px-0 group-data-[collapsible=icon]:items-center">
        <Logo variant="responsive" />
        <div className="mt-4 group-data-[collapsible=icon]:hidden">
          {memberships.length > 1 ? (
            <Select
              value={org?.id}
              onValueChange={(id) => {
                setOrg(id)
                if (location.pathname !== "/oauth/consent")
                  navigate({ to: "/" })
              }}
            >
              <SelectTrigger aria-label="当前组织">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {memberships.map(({ org }) => (
                  <SelectItem key={org.id} value={org.id}>
                    {org.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : (
            <p className="text-xs text-muted-foreground">{org?.name}</p>
          )}
        </div>
      </SidebarHeader>
      <SidebarContent>
        <Main items={items} />
        {roles.includes("org_admin") && (
          <nav aria-label="管理菜单">
            <SidebarGroup>
              <SidebarGroupLabel>管理</SidebarGroupLabel>
            </SidebarGroup>
            <Main
              items={[
                { icon: ListChecks, title: "首页", path: "/admin" },
                { icon: Users, title: "人员", path: "/admin/people" },
                { icon: ListChecks, title: "能力与规则", path: "/admin/rules" },
                { icon: Settings, title: "数据与 AI", path: "/admin/data-ai" },
                { icon: Settings, title: "连接", path: "/admin/connections" },
              ]}
            />
            <details className="group mx-2">
              <summary className="cursor-pointer rounded-md px-2 py-2 text-sm text-muted-foreground hover:bg-sidebar-accent">
                更多
              </summary>
              <Main
                items={[
                  { icon: ScrollText, title: "审计日志", path: "/admin/audit" },
                  {
                    icon: ListChecks,
                    title: "首次设置",
                    path: "/setup-wizard",
                    badge:
                      setup.data?.completed === false ? "待完成" : undefined,
                  },
                ]}
              />
            </details>
          </nav>
        )}
      </SidebarContent>
      <SidebarFooter>
        <SidebarAppearance />
        <User user={currentUser} />
      </SidebarFooter>
    </Sidebar>
  )
}

export default AppSidebar
