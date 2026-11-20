import { useLocation, useNavigate } from "@tanstack/react-router"
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
import { useOrg } from "@/hooks/useOrg"
import { cn } from "@/lib/utils"
import { Main } from "./Main"
import type { useWorkspaceNavigation } from "./SpacePanel"
import { User } from "./User"

export function AppSidebar({
  space,
  destinations,
  isAssistant,
}: Pick<
  ReturnType<typeof useWorkspaceNavigation>,
  "space" | "destinations" | "isAssistant"
>) {
  const { user } = useAuth()
  const { org, memberships, setOrg, isPending } = useOrg()
  const navigate = useNavigate()
  const location = useLocation()
  const orgMark = (
    <span
      className={cn(
        "flex items-center justify-center text-sm font-semibold",
        isAssistant
          ? "size-9 rounded-xl bg-primary text-primary-foreground"
          : "size-[34px] rounded-lg bg-foreground text-background",
      )}
    >
      {org?.name.slice(0, 1) || "企"}
    </span>
  )
  return (
    <Sidebar
      collapsible="none"
      className={cn(
        "sticky top-0 h-dvh shrink-0 border-r",
        isAssistant
          ? "w-16!"
          : "w-[60px]! border-surface-border bg-workspace-rail [&_a]:h-[46px] [&_a]:gap-0.5 [&_a]:text-[11px] [&_a[aria-current=page]]:bg-surface [&_a[aria-current=page]]:text-blue-700 [&_a[aria-current=page]]:shadow-sm [&_a[aria-current=page]]:ring-1 [&_a[aria-current=page]]:ring-surface-border dark:[&_a[aria-current=page]]:text-blue-300 [&_a>svg]:size-[17px]",
      )}
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
              aria-label="切换企业"
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
      <SidebarContent className={isAssistant ? "gap-0 px-1" : "gap-0 px-1.5"}>
        {!isPending && (
          <nav aria-label="主导航" className="space-y-1">
            <Main space="collab" active={space} {...destinations.collab} />
            <Main space="org" active={space} {...destinations.org} />
            {destinations.plan && (
              <Main space="plan" active={space} {...destinations.plan} />
            )}
          </nav>
        )}
      </SidebarContent>
      <SidebarFooter
        className={cn("shrink-0 gap-1 py-2", isAssistant ? "px-1" : "px-1.5")}
      >
        <Main space="settings" active={space} {...destinations.settings} />
        <User user={user} />
      </SidebarFooter>
    </Sidebar>
  )
}
export default AppSidebar
