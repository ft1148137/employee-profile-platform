import { createFileRoute, redirect } from "@tanstack/react-router"

export const Route = createFileRoute("/_layout/admin/accounts")({
  beforeLoad: () => {
    throw redirect({
      to: "/admin/people",
      search: { tab: "accounts" },
      replace: true,
    })
  },
})
