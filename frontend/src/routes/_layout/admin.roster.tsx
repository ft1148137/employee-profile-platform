import { createFileRoute, redirect } from "@tanstack/react-router"

export const Route = createFileRoute("/_layout/admin/roster")({
  beforeLoad: () => {
    throw redirect({
      to: "/admin/assistant",
      replace: true,
    })
  },
})
