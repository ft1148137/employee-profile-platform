import { Link, useLocation } from "@tanstack/react-router"
import { ChevronRight } from "lucide-react"
import { useBotConversations } from "@/components/Bot/SessionHistory"
import { useAdminConversations } from "@/components/Sidebar/AdminConversations"
import { buttonVariants } from "@/components/ui/button"
import { useAssistantWorkspace } from "@/hooks/useAssistantWorkspace"
import { useCurrentConversation } from "@/hooks/useCurrentConversation"
import { useSetup } from "@/hooks/useSetup"

export function AssistantHeader() {
  const { org, mode, canSwitch, storageKey } = useAssistantWorkspace()
  const { search } = useLocation()
  const admin = mode === "admin"
  const adminHistory = useAdminConversations(
    admin && storageKey ? org?.id : undefined,
  )
  const personalHistory = useBotConversations(
    !admin && storageKey ? org?.id : undefined,
  )
  const setup = useSetup()
  const remembered = useCurrentConversation(storageKey)
  const selected = search.session ?? (admin ? undefined : remembered)
  const entry = (admin ? adminHistory : personalHistory).data?.data.find(
    (item) => item.id === selected,
  )
  const title = search.draft
    ? "项目草稿"
    : entry?.purpose === "setup" && !setup.data?.completed
      ? "首次设置"
      : entry?.preview || (selected && admin ? "对话" : "新对话")

  return (
    <>
      <nav
        aria-label="面包屑"
        className="flex min-w-0 flex-1 items-center gap-2 text-sm"
      >
        <h1 className="shrink-0 font-medium">
          {admin ? "管理助手" : "工作助手"}
        </h1>
        {storageKey && (
          <>
            <ChevronRight
              aria-hidden="true"
              className="size-4 shrink-0 text-muted-foreground"
            />
            <span
              aria-current="page"
              className="truncate text-muted-foreground"
            >
              {title}
            </span>
          </>
        )}
      </nav>
      {canSwitch && !search.draft && (
        <nav
          aria-label="助手模式"
          className="flex shrink-0 gap-0.5 rounded-lg border p-0.5"
        >
          {(
            [
              ["admin", "企业管理"],
              ["personal", "个人与团队"],
            ] as const
          ).map(([value, label]) => (
            <Link
              key={value}
              to="/assistant"
              search={{ mode: value }}
              aria-current={mode === value ? "page" : undefined}
              activeOptions={{ exact: true, explicitUndefined: true }}
              className={buttonVariants({
                size: "sm",
                variant: mode === value ? "secondary" : "ghost",
                className: "h-7 px-1.5 text-xs sm:px-2.5",
              })}
            >
              {label}
            </Link>
          ))}
        </nav>
      )}
    </>
  )
}
