import { useNavigate } from "@tanstack/react-router"
import { Settings, UserRound, Users } from "lucide-react"
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
import { useOrg } from "@/hooks/useOrg"
import { type Item, Main } from "./Main"
import { User } from "./User"

export function AppSidebar() {
  const { user: currentUser } = useAuth()

  const { org, memberships, roles, employeeId, setOrg } = useOrg()
  const navigate = useNavigate()
  const items: Item[] = []
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
          <SidebarGroup>
            <SidebarGroupLabel>管理</SidebarGroupLabel>
          </SidebarGroup>
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
