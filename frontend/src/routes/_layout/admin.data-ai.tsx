import { createFileRoute } from "@tanstack/react-router"
import { AdminTabs, tabSearch } from "@/components/Admin/AdminTabs"
import { BotPage } from "@/components/Admin/pages/BotPage"
import { ModelPage } from "@/components/Admin/pages/ModelPage"
import { SourcesPage } from "@/components/Admin/pages/SourcesPage"

const tabs = [
  { value: "sources", title: "数据来源", component: SourcesPage },
  { value: "model", title: "模型", component: ModelPage },
  { value: "bot", title: "内置机器人", component: BotPage },
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
      description="接入工作记录，配置用于起草画像和对话的模型。"
      tabs={tabs}
      tab={tab}
      deniedMessage={
        tab === "model" || tab === "bot"
          ? undefined
          : "你没有权限管理数据来源，只有企业管理员可以操作。"
      }
      onTabChange={(tab) => navigate({ search: { tab } })}
    />
  )
}
