import { createFileRoute, useLocation } from "@tanstack/react-router"
import { useMemo } from "react"
import { AdminAssistantPanel } from "@/components/AdminAssistant/AdminAssistantPanel"
import { AssistantLayout } from "@/components/Chat/AssistantLayout"
import { OrgState } from "@/components/Profile/OrgState"
import {
  AdminConversations,
  useAdminConversations,
} from "@/components/Sidebar/AdminConversations"
import useAuth from "@/hooks/useAuth"
import { useCurrentConversation } from "@/hooks/useCurrentConversation"
import { useOrg } from "@/hooks/useOrg"
import { useSetup } from "@/hooks/useSetup"

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
  const setup = useSetup()
  const location = useLocation()
  const storageKey = `admin-assistant:${user?.id}:${org?.id}`
  const remembered = useCurrentConversation(storageKey)
  const history = useAdminConversations(
    roles.includes("org_admin") ? org?.id : undefined,
  )
  const { prompt, session } = Route.useSearch()
  const prefill = useMemo(() => ({ text: prompt ?? "" }), [prompt])
  if (!org || !user) return <OrgState />
  if (!roles.includes("org_admin"))
    return <p role="alert">需要企业管理员权限</p>
  const initial = history.data?.data.find((entry) => entry.purpose === "setup")
  const selected = session || remembered
  const title =
    selected === initial?.id && !setup.data?.completed
      ? "首次设置"
      : selected
        ? history.data?.data.find((entry) => entry.id === selected)?.preview ||
          "对话"
        : "新对话"
  return (
    <AssistantLayout
      label="管理助手"
      title={title}
      conversations={(close) => (
        <AdminConversations
          orgId={org.id}
          storageKey={storageKey}
          onSelect={close}
        />
      )}
    >
      <AdminAssistantPanel
        key={`${user.id}:${org.id}`}
        orgId={org.id}
        storageKey={storageKey}
        sessionId={session}
        navigationKey={location.state.__TSR_key}
        prefill={prefill}
      />
    </AssistantLayout>
  )
}
