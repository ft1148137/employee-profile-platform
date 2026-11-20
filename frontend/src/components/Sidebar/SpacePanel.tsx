import { Link, useLocation, useNavigate } from "@tanstack/react-router"
import {
  Bell,
  BookOpen,
  Bot,
  BriefcaseBusiness,
  ChartGantt,
  Cpu,
  Database,
  FileText,
  Gauge,
  Inbox,
  Link2,
  type LucideIcon,
  MessageSquare,
  Network,
  Plug,
  Plus,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  SquareCheck,
  UserRound,
  Users,
} from "lucide-react"
import { SessionHistory } from "@/components/Bot/SessionHistory"
import { AdminConversations } from "@/components/Sidebar/AdminConversations"
import { Button } from "@/components/ui/button"
import {
  type AssistantMode,
  useAssistantWorkspace,
} from "@/hooks/useAssistantWorkspace"
import {
  rememberConversation,
  useCurrentConversation,
} from "@/hooks/useCurrentConversation"
import { useInbox } from "@/hooks/useInbox"
import { useOrg } from "@/hooks/useOrg"
import { usePendingApprovals } from "@/hooks/usePendingApprovals"
import { cn } from "@/lib/utils"

export const spaceTitles = {
  collab: "协同",
  org: "组织",
  plan: "规划",
  settings: "设置",
}

export type Space = keyof typeof spaceTitles

type Item = {
  title: string
  icon: LucideIcon
  to: string
  search?:
    | { tab: string }
    | { mode: AssistantMode }
    | { section: "account" | "integrations" | "preferences" }
  badge?: number
}

type Group = { title?: string; items: Item[] }

function tabItem(to: string, tab: string, title: string, icon: LucideIcon) {
  return { to, title, icon, search: { tab } }
}

export function useWorkspaceNavigation() {
  const { org, roles, employeeId } = useOrg()
  const inbox = useInbox()
  const { capacity, totalCount: pending } = usePendingApprovals()
  const { pathname, search } = useLocation()
  const path = pathname.replace(/\/$/, "") || "/"
  const isAdmin = roles.includes("org_admin")
  const isManager = roles.includes("dept_manager")
  const managesEmployees = isAdmin || isManager
  const assistant = "/assistant"
  const { searchMode } = useAssistantWorkspace()
  const assistantSearch = searchMode ? { mode: searchMode } : undefined
  const ownProfile = employeeId ? `/employees/${employeeId}` : undefined
  const unread = inbox.data?.unread_count ?? 0
  const dictionary = tabItem("/admin/rules", "dictionary", "能力词典", BookOpen)
  const canPlan = capacity.data?.enabled && (employeeId || managesEmployees)
  const myLoad = tabItem("/capacity", "mine", "我的负荷", Gauge)
  const teamLoad = tabItem("/capacity", "team", "团队负荷", ChartGantt)

  const groups: Record<Space, Group[]> = {
    collab: [
      {
        items: [
          ...(isAdmin || employeeId
            ? [
                {
                  title: "AI 助手",
                  icon: Sparkles,
                  to: assistant,
                  search: assistantSearch,
                },
              ]
            : []),
          ...(org
            ? [{ title: "收件箱", icon: Inbox, to: "/inbox", badge: unread }]
            : []),
          ...(managesEmployees || pending
            ? [
                {
                  title: "待审批",
                  icon: SquareCheck,
                  to: "/reviews",
                  badge: pending,
                },
              ]
            : []),
        ],
      },
    ],
    org: [
      {
        title: "成员与架构",
        items: [
          ...(ownProfile
            ? [{ title: "我的画像", icon: UserRound, to: ownProfile }]
            : []),
          ...(managesEmployees
            ? [
                {
                  title: isAdmin ? "员工" : "我的团队",
                  icon: Users,
                  to: "/employees",
                },
                { title: "组织架构", icon: Network, to: "/org-chart" },
              ]
            : []),
        ],
      },
      {
        title: "能力与规则",
        items: [
          dictionary,
          ...(isAdmin
            ? [
                tabItem("/admin/rules", "approval", "审批规则", ShieldCheck),
                tabItem("/admin/rules", "reminders", "定期提醒", Bell),
                tabItem(
                  "/admin/rules",
                  "capacity",
                  "负荷与需求规则",
                  SlidersHorizontal,
                ),
              ]
            : []),
        ],
      },
    ],
    plan: canPlan
      ? [
          {
            items: [
              ...(employeeId ? [myLoad] : []),
              ...(managesEmployees
                ? [
                    teamLoad,
                    tabItem(
                      "/capacity",
                      "borrows",
                      "借调请求",
                      BriefcaseBusiness,
                    ),
                  ]
                : []),
            ],
          },
        ]
      : [],
    settings: [
      {
        title: "个人",
        items: [
          {
            title: "账号与安全",
            icon: UserRound,
            to: "/settings",
            search: { section: "account" },
          },
          {
            title: "连接与集成",
            icon: Link2,
            to: "/settings",
            search: { section: "integrations" },
          },
          {
            title: "偏好与代理",
            icon: SlidersHorizontal,
            to: "/settings",
            search: { section: "preferences" },
          },
        ],
      },
      ...(isAdmin
        ? [
            {
              title: "企业管理",
              items: [
                tabItem("/admin/data-ai", "sources", "数据来源", Database),
                tabItem("/admin/data-ai", "model", "模型", Cpu),
                tabItem("/admin/data-ai", "bot", "内置助手", Bot),
                tabItem("/admin/data-ai", "im", "IM 入口", MessageSquare),
                tabItem("/admin/data-ai", "mcp", "外部 AI 工具", Plug),
                { title: "审计日志", icon: FileText, to: "/admin/audit" },
              ],
            },
          ]
        : []),
    ],
  }
  const isAssistant = ["/assistant", "/admin/assistant"].includes(path)
  let space: Space = "org"
  if (
    path === "/" ||
    isAssistant ||
    path === "/inbox" ||
    /^\/reviews(?:\/|$)/.test(path)
  ) {
    space = "collab"
  } else if (path === "/capacity") {
    space = "plan"
  } else if (
    ["/settings", "/admin/data-ai", "/admin/audit", "/oauth/consent"].includes(
      path,
    )
  ) {
    space = "settings"
  }

  const pagePath = isAssistant
    ? assistant
    : path.startsWith("/reviews/")
      ? "/reviews"
      : path
  const candidates = groups[space]
    .flatMap((group) => group.items)
    .filter((item) => item.to === pagePath)
  const active =
    candidates.find(
      (item) =>
        !item.search ||
        ("tab" in item.search
          ? item.search.tab === search.tab
          : "section" in item.search
            ? item.search.section === search.section
            : true),
    ) ??
    (path !== "/capacity" ||
    !["incoming", "outgoing", "projects"].includes(String(search.tab))
      ? candidates[0]
      : undefined)

  const pageTitle =
    active?.title ??
    (path.startsWith("/employees/")
      ? "员工详情"
      : path === "/capacity"
        ? "协作与负荷"
        : path === "/find-people"
          ? "找人"
          : path === "/oauth/consent"
            ? "应用授权"
            : undefined)

  return {
    space,
    pageTitle,
    isAssistant,
    groups: groups[space],
    active,
    destinations: {
      collab: {
        to: assistant,
        search: assistantSearch,
        badge: unread + pending,
      },
      org: ownProfile
        ? { to: ownProfile }
        : managesEmployees
          ? { to: "/employees" }
          : dictionary,
      plan: canPlan ? (employeeId ? myLoad : teamLoad) : undefined,
      settings: { to: "/settings" },
    },
  }
}

export function SpacePanel({
  space,
  groups,
  active,
  isAssistant,
  onNavigate,
}: Pick<
  ReturnType<typeof useWorkspaceNavigation>,
  "space" | "groups" | "active" | "isAssistant"
> & {
  onNavigate?: () => void
}) {
  const { org, mode, searchMode, storageKey } = useAssistantWorkspace()
  const { pathname, search } = useLocation()
  const navigate = useNavigate()
  const remembered = useCurrentConversation(storageKey)
  const session =
    pathname === "/assistant" &&
    !search.draft &&
    typeof search.session === "string"
      ? search.session
      : undefined
  const showHistory = space === "collab" && org && storageKey

  return (
    <nav
      aria-label="空间导航"
      className={cn(
        "min-h-0 flex-1 pb-4",
        isAssistant ? "px-3" : "px-2",
        showHistory ? "flex flex-col" : "overflow-y-auto",
      )}
    >
      <p
        className={cn(
          "font-semibold tracking-tight",
          isAssistant
            ? "px-2 py-4 text-lg"
            : "-mx-2 mb-2.5 flex h-12 shrink-0 items-center justify-between gap-2 border-surface-border border-b px-3.5 pr-10 text-sm md:pr-3.5",
        )}
      >
        <span className="shrink-0">{spaceTitles[space]}</span>
        {!isAssistant && org && (
          <span
            className="truncate rounded bg-muted px-1.5 py-0.5 text-[11px] font-normal text-muted-foreground"
            title={org.name}
          >
            {org.name}
          </span>
        )}
      </p>
      {groups
        .filter((group) => group.items.length)
        .map(({ title, items }) => (
          <div
            className={cn("shrink-0", isAssistant ? "mb-5" : "mb-3.5")}
            key={title ?? space}
          >
            {title && (
              <p className="mb-1 px-2 text-xs font-medium text-muted-foreground">
                {title}
              </p>
            )}
            <ul className="space-y-0.5">
              {items.map((item) => (
                <li key={item.title} className="flex items-center gap-1">
                  <Link
                    to={item.to}
                    search={item.search}
                    activeOptions={{ exact: true }}
                    aria-current={item === active ? "page" : undefined}
                    onClick={onNavigate}
                    className={cn(
                      "flex min-w-0 flex-1 items-center gap-2 rounded-md hover:bg-sidebar-accent focus-visible:outline-2 focus-visible:outline-ring",
                      isAssistant
                        ? "min-h-9 border-transparent border-l-2 px-2.5 py-1 text-sm aria-[current=page]:border-primary aria-[current=page]:bg-primary/10 aria-[current=page]:font-medium aria-[current=page]:text-foreground"
                        : "min-h-8 px-2.5 py-1 text-[13px] text-muted-foreground aria-[current=page]:bg-blue-50 aria-[current=page]:font-semibold aria-[current=page]:text-blue-700 dark:aria-[current=page]:bg-blue-500/15 dark:aria-[current=page]:text-blue-300",
                    )}
                  >
                    {!isAssistant && (
                      <item.icon
                        className="size-3.5 shrink-0"
                        aria-hidden="true"
                      />
                    )}
                    <span>{item.title}</span>
                    {!!item.badge && (
                      <span className="ml-auto rounded-full bg-primary/10 px-1.5 text-xs tabular-nums text-primary">
                        {item.badge}
                      </span>
                    )}
                  </Link>
                  {item.to === "/assistant" && storageKey && (
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      aria-label="新对话"
                      title="新对话"
                      onClick={() => {
                        rememberConversation(storageKey, "")
                        navigate({
                          to: "/assistant",
                          search: { mode: searchMode },
                          state: { assistantRestart: Date.now() },
                        })
                        onNavigate?.()
                      }}
                    >
                      <Plus aria-hidden="true" />
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          </div>
        ))}
      {showHistory && (
        <>
          <h2 className="mb-1 px-2 text-xs font-medium text-muted-foreground">
            最近对话
          </h2>
          {mode === "admin" ? (
            <AdminConversations
              orgId={org.id}
              selected={session}
              onSelect={onNavigate}
            />
          ) : (
            <SessionHistory
              orgId={org.id}
              currentId={
                pathname === "/assistant" && !search.draft
                  ? (session ?? remembered)
                  : undefined
              }
              explicitMode={searchMode === "personal"}
              onSelect={onNavigate}
            />
          )}
        </>
      )}
    </nav>
  )
}
