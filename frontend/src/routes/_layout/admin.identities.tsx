import { createFileRoute, redirect } from "@tanstack/react-router"

export const Route = createFileRoute("/_layout/admin/identities")({
  beforeLoad: () => {
    throw redirect({
      to: "/admin/people",
      search: { tab: "identities" },
      replace: true,
    })
  },
})
