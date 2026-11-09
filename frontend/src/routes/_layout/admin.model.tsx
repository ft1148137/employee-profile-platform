import { createFileRoute, redirect } from "@tanstack/react-router"

export const Route = createFileRoute("/_layout/admin/model")({
  beforeLoad: () => {
    throw redirect({
      to: "/admin/data-ai",
      search: { tab: "model" },
      replace: true,
    })
  },
})
