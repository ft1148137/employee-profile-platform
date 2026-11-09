import { createFileRoute, redirect } from "@tanstack/react-router"

export const Route = createFileRoute("/_layout/admin/appeals")({
  beforeLoad: () => {
    throw redirect({
      to: "/admin/people",
      search: { tab: "appeals" },
      replace: true,
    })
  },
})
