import { Link, useLocation, useNavigate } from "@tanstack/react-router"
import {
  BookOpen,
  Inbox,
  ListChecks,
  MessageCircle,
  MoreHorizontal,
  Network,
  ScrollText,
  Settings,
  Sparkles,
  UserRound,
  Users,
} from "lucide-react"
import { useEffect, useRef, useState } from "react"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from "@/components/ui/select"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
} from "@/components/ui/sidebar"
import useAuth from "@/hooks/useAuth"
import { useInbox } from "@/hooks/useInbox"
import { useOrg } from "@/hooks/useOrg"
import { useReviews } from "@/hooks/useReviews"
import { type Item, Main, railItemClass } from "./Main"
import { User } from "./User"

const manual: Item[] = [
  { icon: BookOpen, title: "能力与规则", path: "/admin/rules" },
  { icon: Sparkles, title: "数据与 AI", path: "/admin/data-ai" },
]

export function AppSidebar() {
  const { user } = useAuth()
  const { org, memberships, roles, employeeId, setOrg } = useOrg()
  const inbox = useInbox()
  const reviews = useReviews()
  const navigate = useNavigate()
  const location = useLocation()
  const administrator = roles.includes("org_admin")
  const content = useRef<HTMLDivElement>(null)
  const [slots, setSlots] = useState(11)
  useEffect(() => {
    if (!content.current) return
    const observer = new ResizeObserver(([entry]) => {
      setSlots(Math.floor(entry.contentRect.height / 48))
    })
    observer.observe(content.current)
    return () => observer.disconnect()
  }, [])
  const items: Item[] = []
  if (administrator)
    items.push({ icon: MessageCircle, title: "聊天", path: "/admin/assistant" })
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
  if (administrator || roles.includes("dept_manager"))
    items.push(
      { icon: Network, title: "组织架构", path: "/org-chart" },
      { icon: Users, title: "员工", path: "/employees" },
    )
  const visible = Math.max(0, Math.min(manual.length, slots - items.length - 1))
  const more: Item[] = [
    ...manual.slice(visible),
    { icon: ScrollText, title: "审计日志", path: "/admin/audit" },
  ]
  const orgMark = (
    <span className="flex size-9 items-center justify-center rounded-xl bg-primary text-sm font-semibold text-primary-foreground">
      {org?.name.slice(0, 1) || "企"}
    </span>
  )
  return (
    <Sidebar
      collapsible="none"
      className="sticky top-0 h-dvh w-16! shrink-0 border-r"
      data-testid="app-rail"
    >
      <SidebarHeader className="h-14 shrink-0 items-center justify-center p-0">
        {memberships.length > 1 ? (
          <Select
            value={org?.id}
            onValueChange={(id) => {
              setOrg(id)
              if (location.pathname !== "/oauth/consent") navigate({ to: "/" })
            }}
          >
            <SelectTrigger
              aria-label="当前组织"
              title={org?.name}
              className="size-10 border-0 p-0 shadow-none [&>svg]:hidden"
            >
              {orgMark}
              <span className="sr-only">{org?.name}</span>
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
          <div title={org?.name} role="img" aria-label={org?.name || "企业"}>
            {orgMark}
          </div>
        )}
      </SidebarHeader>
      <SidebarContent ref={content} className="gap-0 overflow-hidden px-1">
        <nav aria-label="主导航">
          <Main items={items} />
        </nav>
        {administrator && (
          <>
            <nav aria-label="管理菜单">
              <Main items={manual.slice(0, visible)} />
            </nav>
            <DropdownMenu>
              <DropdownMenuTrigger
                className={`${railItemClass} data-[active=true]:bg-sidebar-accent data-[active=true]:text-sidebar-accent-foreground`}
                aria-label="更多"
                data-active={more.some(
                  (item) => item.path === location.pathname,
                )}
              >
                <MoreHorizontal className="size-5" />
                <span>更多</span>
              </DropdownMenuTrigger>
              <DropdownMenuContent side="right" align="end">
                {more.map((item) => (
                  <DropdownMenuItem key={item.path} asChild>
                    <Link to={item.path}>
                      <item.icon />
                      {item.title}
                      {item.badge && (
                        <span className="ml-auto text-xs text-muted-foreground">
                          {item.badge}
                        </span>
                      )}
                    </Link>
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          </>
        )}
      </SidebarContent>
      <SidebarFooter className="shrink-0 gap-1 px-1 py-2">
        <Main items={[{ icon: Settings, title: "设置", path: "/settings" }]} />
        <User user={user} />
      </SidebarFooter>
    </Sidebar>
  )
}
export default AppSidebar
