import { createFileRoute, Outlet, redirect } from "@tanstack/react-router"
import axios, { AxiosError } from "axios"
import { UsersService } from "@/client"
import { BotPanel } from "@/components/Bot/BotPanel"
import AppSidebar from "@/components/Sidebar/AppSidebar"
import {
  SidebarInset,
  SidebarProvider,
  SidebarTrigger,
} from "@/components/ui/sidebar"
import { isLoggedIn } from "@/hooks/useAuth"

export const Route = createFileRoute("/_layout")({
  component: Layout,
  beforeLoad: async () => {
    if (!isLoggedIn()) {
      throw redirect({
        to: "/login",
      })
    }
    try {
      // The route guard handles auth errors without the global reload interceptor.
      const { data: user } = await UsersService.readUserMe({ axios })
      if (user.must_change_password) {
        throw redirect({ to: "/change-password" })
      }
    } catch (error) {
      if (
        error instanceof AxiosError &&
        [401, 403].includes(error.response?.status ?? 0)
      ) {
        if (error.response?.data?.detail?.code === "password_change_required") {
          throw redirect({ to: "/change-password" })
        }
        localStorage.removeItem("access_token")
        throw redirect({ to: "/login" })
      }
      throw error
    }
  },
})

function Layout() {
  return (
    <SidebarProvider>
      <AppSidebar />
      <SidebarInset>
        <header className="sticky top-0 z-10 flex h-16 shrink-0 items-center gap-2 border-b bg-background px-4">
          <SidebarTrigger className="-ml-1 text-muted-foreground" />
        </header>
        <main className="flex-1 p-6 md:p-8">
          <div className="mx-auto max-w-7xl">
            <Outlet />
          </div>
        </main>
      </SidebarInset>
      <BotPanel />
    </SidebarProvider>
  )
}
