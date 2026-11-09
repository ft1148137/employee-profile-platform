import { createFileRoute, redirect } from "@tanstack/react-router"

export const Route = createFileRoute("/_layout/admin/progress")({
  beforeLoad: () => {
    throw redirect({ to: "/admin", replace: true })
  },
})
