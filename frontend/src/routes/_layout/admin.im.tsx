import { createFileRoute, redirect } from "@tanstack/react-router"

export const Route = createFileRoute("/_layout/admin/im")({
  beforeLoad: () => {
    throw redirect({
      to: "/admin/data-ai",
      search: { tab: "im" },
      replace: true,
    })
  },
})
