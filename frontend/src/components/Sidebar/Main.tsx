import { Link } from "@tanstack/react-router"
import { CalendarRange, MessagesSquare, Network, Settings } from "lucide-react"
import type { AssistantMode } from "@/hooks/useAssistantWorkspace"
import { type Space, spaceTitles } from "./SpacePanel"

const icons = {
  collab: MessagesSquare,
  org: Network,
  plan: CalendarRange,
  settings: Settings,
}

export function Main({
  space,
  active,
  to,
  search,
  badge,
}: {
  space: Space
  active: Space
  to: string
  search?: { tab: string } | { mode: AssistantMode }
  badge?: number
}) {
  const Icon = icons[space]
  return (
    <Link
      to={to}
      search={search}
      activeOptions={{ exact: true }}
      aria-current={space === active ? "page" : undefined}
      className="relative flex h-12 w-full shrink-0 flex-col items-center justify-center gap-1 rounded-lg text-xs text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:outline-2 focus-visible:outline-ring aria-[current=page]:bg-primary/10 aria-[current=page]:text-primary"
    >
      <Icon className="size-5" aria-hidden="true" />
      <span>{spaceTitles[space]}</span>
      {!!badge && (
        <span className="absolute right-0.5 top-0.5 rounded-full bg-primary px-1 text-[10px] leading-4 tabular-nums text-primary-foreground">
          {badge}
        </span>
      )}
    </Link>
  )
}
