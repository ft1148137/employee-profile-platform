import { createFileRoute, redirect } from "@tanstack/react-router"

export const Route = createFileRoute("/_layout/admin/roles")({
  beforeLoad: () => {
    throw redirect({
      to: "/employees",
      search: { filter: "roles" },
      replace: true,
    })
  },
})
