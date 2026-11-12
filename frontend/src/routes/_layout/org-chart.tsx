import { createFileRoute } from "@tanstack/react-router"
import { OrgChart } from "@/components/OrgChart/OrgChart"

export const Route = createFileRoute("/_layout/org-chart")({
  component: OrgChart,
  head: () => ({ meta: [{ title: "组织架构 - 员工画像平台" }] }),
})
