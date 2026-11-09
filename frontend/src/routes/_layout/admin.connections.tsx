import { createFileRoute } from "@tanstack/react-router"
import { AdminTabs, tabSearch } from "@/components/Admin/AdminTabs"
import { AssistantsPage } from "@/components/Admin/pages/AssistantsPage"
import { ImPage } from "@/components/Admin/pages/ImPage"

const tabs = [
  { value: "im", title: "IM 入口", component: ImPage },
  { value: "mcp", title: "外部 AI 助手", component: AssistantsPage },
]

export const Route = createFileRoute("/_layout/admin/connections")({
  component: Connections,
  validateSearch: tabSearch,
  head: () => ({ meta: [{ title: "连接 - 员工画像平台" }] }),
})

function Connections() {
  const { tab } = Route.useSearch()
  const navigate = Route.useNavigate()
  return (
    <AdminTabs
      title="连接"
      description="让员工通过聊天工具或自己的 AI 助手使用平台。"
      tabs={tabs}
      tab={tab}
      onTabChange={(tab) => navigate({ search: { tab } })}
    />
  )
}
