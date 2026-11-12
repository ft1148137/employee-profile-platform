import {
  createFileRoute,
  Outlet,
  redirect,
  useLocation,
} from "@tanstack/react-router"
import axios, { AxiosError } from "axios"
import { UsersService } from "@/client"
import { BotPanel } from "@/components/Bot/BotPanel"
import AppSidebar from "@/components/Sidebar/AppSidebar"
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar"
import { isLoggedIn } from "@/hooks/useAuth"
import { useOrg } from "@/hooks/useOrg"

export const Route = createFileRoute("/_layout")({
  component: Layout,
  beforeLoad: async ({ location }) => {
    const search =
      location.pathname === "/oauth/consent"
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
  const location = useLocation()
  const { roles } = useOrg()
  const assistant = location.pathname.replace(/\/$/, "") === "/admin/assistant"
  return (
    <SidebarProvider>
      <AppSidebar />
      <SidebarInset className="min-w-0">
        {!assistant && (
          <header className="sticky top-0 z-10 h-16 shrink-0 border-b bg-background" />
        )}
        <main
          className={
            assistant ? "flex min-h-0 flex-1 flex-col" : "flex-1 p-6 md:p-8"
          }
        >
          <div className={assistant ? "h-dvh min-h-0" : "mx-auto max-w-7xl"}>
            <Outlet />
          </div>
        </main>
      </SidebarInset>
      {(!roles.includes("org_admin") ||
        !/^\/admin(?:\/|$)/.test(location.pathname)) && <BotPanel />}
    </SidebarProvider>
  )
}
