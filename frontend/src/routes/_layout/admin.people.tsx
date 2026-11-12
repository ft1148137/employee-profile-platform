import { createFileRoute, Outlet, redirect } from "@tanstack/react-router"
import { tabSearch } from "@/components/Admin/AdminTabs"

export const Route = createFileRoute("/_layout/admin/people")({
  component: Outlet,
  validateSearch: tabSearch,
  beforeLoad: ({ search, location }) => {
    if (location.pathname.replace(/\/$/, "") !== "/admin/people") return
    if (search.tab === "roster")
      throw redirect({ to: "/admin/assistant", replace: true })
    if (search.tab === "org-chart")
      throw redirect({ href: "/org-chart", replace: true })
    throw redirect({
      to: "/employees",
      search: {
        filter: ["roles", "identities", "appeals"].includes(search.tab ?? "")
          ? search.tab
          : undefined,
      },
      replace: true,
    })
  },
})
