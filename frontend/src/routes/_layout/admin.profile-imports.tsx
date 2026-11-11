import { createFileRoute, redirect } from "@tanstack/react-router"

export const Route = createFileRoute("/_layout/admin/profile-imports")({
  beforeLoad: () => {
    throw redirect({
      to: "/admin/assistant",
      replace: true,
    })
  },
})
