import { createFileRoute } from "@tanstack/react-router"
import { AdminTabs, tabSearch } from "@/components/Admin/AdminTabs"
import { AssistantsPage } from "@/components/Admin/pages/AssistantsPage"
import { BotPage } from "@/components/Admin/pages/BotPage"
import { ImPage } from "@/components/Admin/pages/ImPage"
import { ModelPage } from "@/components/Admin/pages/ModelPage"
import { SourcesPage } from "@/components/Admin/pages/SourcesPage"

const tabs = [
  { value: "sources", title: "数据来源", component: SourcesPage },
  { value: "model", title: "模型", component: ModelPage },
  { value: "bot", title: "内置机器人", component: BotPage },
  { value: "im", title: "IM 入口", component: ImPage },
  { value: "mcp", title: "外部 AI 助手", component: AssistantsPage },
]

export const Route = createFileRoute("/_layout/admin/data-ai")({
  component: DataAi,
  validateSearch: tabSearch,
  head: () => ({ meta: [{ title: "数据与 AI - 员工画像平台" }] }),
})

function DataAi() {
  const { tab } = Route.useSearch()
  const navigate = Route.useNavigate()
  return (
    <AdminTabs
      title="数据与 AI"
      description="接入工作记录，配置模型，以及员工通过聊天工具或自己的 AI 助手使用平台的方式。"
      tabs={tabs}
      tab={tab}
      deniedMessage={
        ["model", "bot", "im", "mcp"].includes(tab ?? "")
          ? undefined
          : "你没有权限管理数据来源，只有企业管理员可以操作。"
      }
      onTabChange={(tab) => navigate({ search: { tab } })}
    />
  )
}
