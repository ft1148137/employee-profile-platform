import { createFileRoute } from "@tanstack/react-router"
import { BotConversation } from "@/components/Bot/BotPanel"
import { OrgState } from "@/components/Profile/OrgState"
import useAuth from "@/hooks/useAuth"
import { useOrg } from "@/hooks/useOrg"

export const Route = createFileRoute("/_layout/assistant")({
  component: AssistantPage,
  validateSearch: (
    search: Record<string, unknown>,
  ): { prompt?: string; session?: string } => ({
    prompt: typeof search.prompt === "string" ? search.prompt : undefined,
    session: typeof search.session === "string" ? search.session : undefined,
  }),
  head: () => ({ meta: [{ title: "助手 - 员工画像平台" }] }),
})

function AssistantPage() {
  const { org, employeeId } = useOrg()
  const { user } = useAuth()
  const { prompt, session } = Route.useSearch()
  if (!org || !user) return <OrgState />
  if (!employeeId)
    return <p role="alert">当前账号没有员工身份，请联系管理员。</p>
  return (
    <BotConversation
      key={`${user.id}:${org.id}:${employeeId}`}
      orgId={org.id}
      employeeId={employeeId}
      storageKey={`bot-session:${user.id}:${org.id}:${employeeId}`}
      fullPage
      selectedSession={session}
      prompt={prompt}
    />
  )
}
