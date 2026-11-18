import { createFileRoute } from "@tanstack/react-router"
import { BotConversation } from "@/components/Bot/BotPanel"
import { FeishuProjectDraft } from "@/components/Bot/ProjectDraftMessage"
import { OrgState } from "@/components/Profile/OrgState"
import useAuth from "@/hooks/useAuth"
import { useOrg } from "@/hooks/useOrg"

export const Route = createFileRoute("/_layout/assistant")({
  component: AssistantPage,
  validateSearch: (
    search: Record<string, unknown>,
  ): { prompt?: string; session?: string; draft?: string; org?: string } => ({
    prompt: typeof search.prompt === "string" ? search.prompt : undefined,
    session: typeof search.session === "string" ? search.session : undefined,
    draft: typeof search.draft === "string" ? search.draft : undefined,
    org: typeof search.org === "string" ? search.org : undefined,
  }),
  head: () => ({ meta: [{ title: "助手 - 员工画像平台" }] }),
})

function AssistantPage() {
  const { org, employeeId, memberships } = useOrg()
  const { user } = useAuth()
  const { prompt, session, draft, org: linkedOrg } = Route.useSearch()
  if (!org || !user) return <OrgState />
  if (draft) {
    const membership = memberships.find((item) => item.org.id === linkedOrg)
    return membership?.employee_id ? (
      <FeishuProjectDraft key={draft} orgId={membership.org.id} draft={draft} />
    ) : (
      <p role="alert">无法打开项目草稿，请用绑定飞书的员工账号登录。</p>
    )
  }
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
