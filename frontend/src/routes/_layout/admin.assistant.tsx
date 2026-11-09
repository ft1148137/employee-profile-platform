import { createFileRoute } from "@tanstack/react-router"
import { useState } from "react"
import { TodayPanel } from "@/components/Admin/TodayPanel"
import { AdminAssistantPanel } from "@/components/AdminAssistant/AdminAssistantPanel"
import { OrgState } from "@/components/Profile/OrgState"
import { User } from "@/components/Sidebar/User"
import useAuth from "@/hooks/useAuth"
import { useOrg } from "@/hooks/useOrg"

export const Route = createFileRoute("/_layout/admin/assistant")({
  component: AssistantPage,
  validateSearch: (search: Record<string, unknown>) => ({
    prompt: typeof search.prompt === "string" ? search.prompt : undefined,
  }),
  head: () => ({ meta: [{ title: "管理助手 - 员工画像平台" }] }),
})

function AssistantPage() {
  const { org, roles } = useOrg()
  const { user } = useAuth()
  const { prompt } = Route.useSearch()
  const [prefill, setPrefill] = useState({ text: prompt ?? "" })
  if (!org || !user) return <OrgState />
  if (!roles.includes("org_admin"))
    return <p role="alert">需要企业管理员权限</p>
  return (
    <div className="flex h-dvh w-full flex-col bg-background">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b px-6 py-4">
        <h1 className="text-xl font-semibold">管理助手</h1>
        <a href="/admin" className="text-sm underline">
          切换到手工页面
        </a>
        <div className="w-48">
          <User user={user} />
        </div>
      </header>
      <div className="grid min-h-0 flex-1 lg:grid-cols-[360px_minmax(0,1fr)]">
        <aside className="max-h-[30dvh] overflow-y-auto border-b p-4 lg:max-h-none lg:border-r lg:border-b-0">
          <TodayPanel
            orgId={org.id}
            onPrompt={(text) => setPrefill({ text })}
          />
        </aside>
        <section className="min-h-0 min-w-0" aria-label="管理助手">
          <AdminAssistantPanel
            key={`${user.id}:${org.id}`}
            orgId={org.id}
            storageKey={`admin-assistant:${user.id}:${org.id}`}
            page="/admin"
            fullScreen
            prefill={prefill}
          />
        </section>
      </div>
    </div>
  )
}
