import { createFileRoute, redirect } from "@tanstack/react-router"

export const Route = createFileRoute("/_layout/admin/identities")({
  beforeLoad: () => {
    throw redirect({
      to: "/employees",
      search: { filter: "identities" },
      replace: true,
    })
  },
})
