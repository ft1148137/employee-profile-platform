import { createFileRoute } from "@tanstack/react-router"
import { AdminSection, tabSearch } from "@/components/Admin/AdminTabs"
import { AssistantsPage } from "@/components/Admin/pages/AssistantsPage"
import { BotPage } from "@/components/Admin/pages/BotPage"
import { ImPage } from "@/components/Admin/pages/ImPage"
import { ModelPage } from "@/components/Admin/pages/ModelPage"
import { SourcesPage } from "@/components/Admin/pages/SourcesPage"

const sections = [
  { value: "sources", component: SourcesPage },
  { value: "model", component: ModelPage },
  { value: "bot", component: BotPage },
  { value: "im", component: ImPage },
  { value: "mcp", component: AssistantsPage },
]

export const Route = createFileRoute("/_layout/admin/data-ai")({
  component: DataAi,
  validateSearch: tabSearch,
  head: () => ({ meta: [{ title: "数据与 AI - 员工画像平台" }] }),
})

function DataAi() {
  const { tab } = Route.useSearch()
  return (
    <AdminSection
      sections={sections}
      tab={tab}
      deniedMessage={
        ["model", "bot", "im", "mcp"].includes(tab ?? "")
          ? "需要企业管理员权限"
          : "你没有权限管理数据来源，只有企业管理员可以操作。"
      }
    />
  )
}
