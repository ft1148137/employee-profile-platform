import { createFileRoute, useLocation } from "@tanstack/react-router"
import { ChevronRight, MessagesSquare } from "lucide-react"
import { useMemo, useState } from "react"
import { AdminAssistantPanel } from "@/components/AdminAssistant/AdminAssistantPanel"
import { OrgState } from "@/components/Profile/OrgState"
import {
  AdminConversations,
  useAdminConversations,
} from "@/components/Sidebar/AdminConversations"
import { Button } from "@/components/ui/button"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import useAuth from "@/hooks/useAuth"
import { useCurrentConversation } from "@/hooks/useCurrentConversation"
import { useIsMobile } from "@/hooks/useMobile"
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
  const isMobile = useIsMobile()
  const [open, setOpen] = useState(false)
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
  const conversations = (
    <AdminConversations
      orgId={org.id}
      storageKey={storageKey}
      onSelect={() => setOpen(false)}
    />
  )
  return (
    <div className="flex h-full min-h-0">
      {isMobile ? (
        <Sheet open={open} onOpenChange={setOpen}>
          <SheetContent side="left" className="w-80 gap-0 p-0 [&_header]:pr-12">
            <SheetHeader className="sr-only">
              <SheetTitle>会话</SheetTitle>
              <SheetDescription>选择管理助手会话</SheetDescription>
            </SheetHeader>
            {conversations}
          </SheetContent>
        </Sheet>
      ) : (
        <aside className="w-80 shrink-0 border-r">{conversations}</aside>
      )}
      <section className="flex min-w-0 flex-1 flex-col" aria-label="管理助手">
        <header className="flex h-16 shrink-0 items-center gap-3 border-b px-4 md:px-6">
          {isMobile && (
            <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
              <MessagesSquare className="size-4" />
              会话
            </Button>
          )}
          <nav
            aria-label="面包屑"
            className="flex min-w-0 items-center gap-2 text-sm"
          >
            <h1 className="shrink-0 font-medium">管理助手</h1>
            <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
            <span
              aria-current="page"
              className="truncate text-muted-foreground"
            >
              {title}
            </span>
          </nav>
        </header>
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
    </div>
  )
}
