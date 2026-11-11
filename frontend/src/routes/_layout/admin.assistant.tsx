import { createFileRoute, useLocation } from "@tanstack/react-router"
import { useMemo } from "react"
import { AdminAssistantPanel } from "@/components/AdminAssistant/AdminAssistantPanel"
import { OrgState } from "@/components/Profile/OrgState"
import useAuth from "@/hooks/useAuth"
import { useOrg } from "@/hooks/useOrg"

export const Route = createFileRoute("/_layout/admin/assistant")({
  component: AssistantPage,
  validateSearch: (
    search: Record<string, unknown>,
  ): { prompt?: string; session?: string } => ({
    prompt: typeof search.prompt === "string" ? search.prompt : undefined,
    session: typeof search.session === "string" ? search.session : undefined,
  }),
  head: () => ({ meta: [{ title: "管理助手 - 员工画像平台" }] }),
})

function AssistantPage() {
  const { org, roles } = useOrg()
  const { user } = useAuth()
  const location = useLocation()
  const { prompt, session } = Route.useSearch()
  const prefill = useMemo(() => ({ text: prompt ?? "" }), [prompt])
  if (!org || !user) return <OrgState />
  if (!roles.includes("org_admin"))
    return <p role="alert">需要企业管理员权限</p>
  return (
    <section className="flex h-full min-h-0 flex-col" aria-label="管理助手">
      <h1 className="shrink-0 border-b px-6 py-3 text-lg font-semibold">
        管理助手
      </h1>
      <div className="min-h-0 flex-1">
        <AdminAssistantPanel
          key={`${user.id}:${org.id}`}
          orgId={org.id}
          storageKey={`admin-assistant:${user.id}:${org.id}`}
          sessionId={session}
          navigationKey={location.state.__TSR_key}
          prefill={prefill}
        />
      </div>
    </section>
  )
}
