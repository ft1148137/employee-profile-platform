import { createFileRoute, redirect } from "@tanstack/react-router"

export const Route = createFileRoute("/_layout/admin/assistants")({
  beforeLoad: () => {
    throw redirect({
      to: "/admin/data-ai",
      search: { tab: "mcp" },
      replace: true,
    })
  },
})
