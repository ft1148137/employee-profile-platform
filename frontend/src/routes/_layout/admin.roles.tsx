import { createFileRoute, redirect } from "@tanstack/react-router"

export const Route = createFileRoute("/_layout/admin/roles")({
  beforeLoad: () => {
    throw redirect({
      to: "/admin/people",
      search: { tab: "roles" },
      replace: true,
    })
  },
})
