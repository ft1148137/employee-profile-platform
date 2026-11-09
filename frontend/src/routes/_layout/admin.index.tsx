import { createFileRoute } from "@tanstack/react-router"
import { ProgressPage } from "@/components/Admin/pages/ProgressPage"

export const Route = createFileRoute("/_layout/admin/")({
  component: ProgressPage,
  head: () => ({ meta: [{ title: "管理首页 - 员工画像平台" }] }),
})
