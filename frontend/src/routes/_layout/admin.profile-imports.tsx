import { createFileRoute, redirect } from "@tanstack/react-router"

export const Route = createFileRoute("/_layout/admin/profile-imports")({
  beforeLoad: () => {
    throw redirect({
      to: "/admin/rules",
      search: { tab: "profile-import" },
      replace: true,
    })
  },
})
