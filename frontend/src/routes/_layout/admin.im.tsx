import { createFileRoute, redirect } from "@tanstack/react-router"

export const Route = createFileRoute("/_layout/admin/im")({
  beforeLoad: () => {
    throw redirect({
      to: "/admin/connections",
      search: { tab: "im" },
      replace: true,
    })
  },
})
