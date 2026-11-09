import { createFileRoute, redirect } from "@tanstack/react-router"

export const Route = createFileRoute("/_layout/admin/bot")({
  beforeLoad: () => {
    throw redirect({
      to: "/admin/data-ai",
      search: { tab: "bot" },
      replace: true,
    })
  },
})
