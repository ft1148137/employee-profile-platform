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
  head: () => ({ meta: [{ title: "企业管理 - 员工画像平台" }] }),
})

function DataAi() {
  const { tab } = Route.useSearch()
  return (
    <AdminSection
      sections={sections}
      tab={tab}
      deniedMessage="需要企业管理员权限"
    />
  )
}
