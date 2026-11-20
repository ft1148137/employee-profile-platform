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
import { Main } from "./Main"
import type { useWorkspaceNavigation } from "./SpacePanel"
import { User } from "./User"

export function AppSidebar({
  space,
  destinations,
}: Pick<ReturnType<typeof useWorkspaceNavigation>, "space" | "destinations">) {
  const { user } = useAuth()
  const { org, memberships, setOrg, isPending } = useOrg()
  const navigate = useNavigate()
  const location = useLocation()
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
      <SidebarContent className="gap-0 px-1">
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
      <SidebarFooter className="shrink-0 gap-1 px-1 py-2">
        <Main space="settings" active={space} {...destinations.settings} />
        <User user={user} />
      </SidebarFooter>
    </Sidebar>
  )
}
export default AppSidebar
