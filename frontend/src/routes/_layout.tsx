import { createFileRoute, Outlet, redirect } from "@tanstack/react-router"
import axios, { AxiosError } from "axios"
import { ChevronRight, Menu, PanelLeft } from "lucide-react"
import { UsersService } from "@/client"
import { AssistantHeader } from "@/components/Chat/AssistantHeader"
import { TOPBAR_ACTIONS_ID } from "@/components/Common/TopbarSlot"
import AppSidebar from "@/components/Sidebar/AppSidebar"
import {
  SpacePanel,
  spaceTitles,
  useWorkspaceNavigation,
} from "@/components/Sidebar/SpacePanel"
import { Button } from "@/components/ui/button"
import {
  Sheet,
  SheetContent,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet"
import { SidebarProvider, useSidebar } from "@/components/ui/sidebar"
import { isLoggedIn } from "@/hooks/useAuth"
import { cn } from "@/lib/utils"
import { authReturnPath } from "@/utils"

export const Route = createFileRoute("/_layout")({
  component: Layout,
  beforeLoad: async ({ location }) => {
    if (
      location.pathname.replace(/\/$/, "") === "/employees" &&
      new URLSearchParams(location.searchStr).get("view") === "org-chart"
    )
      throw redirect({ to: "/org-chart", replace: true })
    const search = authReturnPath(location.pathname, location.searchStr)
      ? { next: location.href }
      : undefined
    if (!isLoggedIn()) {
      throw redirect({
        to: "/login",
        search,
      })
    }
    try {
      // The route guard handles auth errors without the global reload interceptor.
      const { data: user } = await UsersService.readUserMe({ axios })
      if (user.must_change_password) {
        throw redirect({ to: "/change-password", search })
      }
    } catch (error) {
      if (
        error instanceof AxiosError &&
        [401, 403].includes(error.response?.status ?? 0)
      ) {
        if (error.response?.data?.detail?.code === "password_change_required") {
          throw redirect({ to: "/change-password", search })
        }
        localStorage.removeItem("access_token")
        throw redirect({ to: "/login", search })
      }
      throw error
    }
  },
})

function Layout() {
  return (
    <SidebarProvider>
      <Workspace />
    </SidebarProvider>
  )
}

function Workspace() {
  const { open, openMobile, setOpenMobile, toggleSidebar, isMobile } =
    useSidebar()
  const navigation = useWorkspaceNavigation()
  const { space, pageTitle, isAssistant } = navigation
  return (
    <>
      <AppSidebar {...navigation} />
      {open && !isMobile && (
        <aside
          id="workspace-navigation"
          className={cn(
            "sticky top-0 hidden h-dvh shrink-0 flex-col border-r md:flex",
            isAssistant
              ? "w-64 bg-sidebar"
              : "w-60 border-surface-border bg-workspace-panel",
          )}
        >
          <SpacePanel {...navigation} />
        </aside>
      )}
      <div
        className={`flex min-w-0 flex-1 flex-col ${isAssistant ? "h-dvh" : ""}`}
      >
        <header
          className={cn(
            "sticky top-0 z-10 flex h-12 shrink-0 items-center gap-2 border-b px-3 md:px-4",
            isAssistant ? "bg-background" : "border-surface-border bg-surface",
          )}
        >
          {isMobile ? (
            <Sheet open={openMobile} onOpenChange={setOpenMobile}>
              <SheetTrigger asChild>
                <Button
                  size="icon-sm"
                  variant="ghost"
                  aria-label="打开空间导航"
                >
                  <Menu aria-hidden="true" />
                </Button>
              </SheetTrigger>
              <SheetContent
                side="left"
                aria-describedby={undefined}
                className={cn(
                  "max-w-full gap-0",
                  isAssistant
                    ? "w-64 bg-sidebar"
                    : "w-60 border-surface-border bg-workspace-panel",
                )}
              >
                <SheetTitle className="sr-only">空间导航</SheetTitle>
                <SpacePanel
                  {...navigation}
                  onNavigate={() => setOpenMobile(false)}
                />
              </SheetContent>
            </Sheet>
          ) : (
            <Button
              size="icon-sm"
              variant="ghost"
              aria-label={open ? "折叠空间导航" : "展开空间导航"}
              aria-expanded={open}
              aria-controls={open ? "workspace-navigation" : undefined}
              onClick={toggleSidebar}
            >
              <PanelLeft aria-hidden="true" />
            </Button>
          )}
          {isAssistant ? (
            <AssistantHeader />
          ) : (
            <>
              <nav
                aria-label="面包屑"
                className="flex min-w-0 items-center gap-1.5 whitespace-nowrap text-[13px]"
              >
                <span className="truncate text-muted-foreground">
                  {spaceTitles[space]}
                </span>
                {pageTitle && (
                  <>
                    <ChevronRight
                      aria-hidden="true"
                      className="size-3 shrink-0 text-muted-foreground"
                    />
                    <span
                      aria-current="page"
                      className="truncate font-semibold"
                    >
                      {pageTitle}
                    </span>
                  </>
                )}
              </nav>
              <div
                id={TOPBAR_ACTIONS_ID}
                className="ml-auto flex min-w-0 max-w-[75%] items-center gap-2 empty:hidden"
              />
            </>
          )}
        </header>
        {/* Keep the outlet mounted while the router resolves the next match. */}
        <main
          data-workspace={isAssistant ? undefined : ""}
          className={
            isAssistant
              ? "min-h-0 flex-1"
              : "min-w-0 flex-1 bg-canvas [&:not(:has([data-workspace-page]))]:p-3 md:[&:not(:has([data-workspace-page]))]:p-4"
          }
        >
          <div className={isAssistant ? "h-full min-h-0" : undefined}>
            <Outlet />
          </div>
        </main>
      </div>
    </>
  )
}
