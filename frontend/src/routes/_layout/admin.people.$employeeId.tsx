import { createFileRoute, redirect } from "@tanstack/react-router"
import { tabSearch } from "@/components/Admin/AdminTabs"

export const Route = createFileRoute("/_layout/admin/people/$employeeId")({
  validateSearch: tabSearch,
  beforeLoad: ({ params, search }) => {
    throw redirect({
      to: "/employees/$employeeId",
      params,
      search: { tab: search.tab ?? "account" },
      replace: true,
    })
  },
})
