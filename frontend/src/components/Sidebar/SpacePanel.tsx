import { Link, useLocation } from "@tanstack/react-router"
import { useCapacitySettings } from "@/components/Capacity/shared"
import { useInbox } from "@/hooks/useInbox"
import { useOrg } from "@/hooks/useOrg"
import { useReviews } from "@/hooks/useReviews"

export const spaceTitles = {
  collab: "协同",
  org: "组织",
  plan: "规划",
  settings: "设置",
}

export type Space = keyof typeof spaceTitles

type Item = {
  title: string
  to: string
  search?:
    | { tab: string }
    | { section: "account" | "integrations" | "preferences" }
  badge?: number
}

type Group = { title?: string; items: Item[] }

function tabItem(to: string, tab: string, title: string) {
  return { to, title, search: { tab } }
}

export function useWorkspaceNavigation() {
  const { org, roles, employeeId } = useOrg()
  const inbox = useInbox()
  const reviews = useReviews()
  const capacity = useCapacitySettings(org?.id)
  const { pathname, search } = useLocation()
  const path = pathname.replace(/\/$/, "") || "/"
  const isAdmin = roles.includes("org_admin")
  const isManager = roles.includes("dept_manager")
  const managesEmployees = isAdmin || isManager
  const assistant = isAdmin ? "/admin/assistant" : "/assistant"
  const ownProfile = employeeId ? `/employees/${employeeId}` : undefined
  const unread = inbox.data?.unread_count ?? 0
  const pending = reviews.data?.count ?? 0
  const dictionary = tabItem("/admin/rules", "dictionary", "能力词典")
  const canPlan = capacity.data?.enabled && (employeeId || managesEmployees)
  const myLoad = tabItem("/capacity", "mine", "我的负荷")
  const teamLoad = tabItem("/capacity", "team", "团队负荷")

  const groups: Record<Space, Group[]> = {
    collab: [
      {
        title: "处理中心",
        items: [
          ...(isAdmin || employeeId
            ? [{ title: "AI 助手", to: assistant }]
            : []),
          ...(org ? [{ title: "收件箱", to: "/inbox", badge: unread }] : []),
          ...(managesEmployees || pending
            ? [{ title: "待审批", to: "/reviews", badge: pending }]
            : []),
        ],
      },
    ],
    org: [
      {
        title: "成员与架构",
        items: [
          ...(ownProfile ? [{ title: "我的画像", to: ownProfile }] : []),
          ...(managesEmployees
            ? [
                { title: isAdmin ? "员工" : "我的团队", to: "/employees" },
                { title: "组织架构", to: "/org-chart" },
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
                tabItem("/admin/rules", "approval", "审批规则"),
                tabItem("/admin/rules", "reminders", "定期提醒"),
                tabItem("/admin/rules", "capacity", "需求与容量规则"),
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
                ? [teamLoad, tabItem("/capacity", "borrows", "借调请求")]
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
            to: "/settings",
            search: { section: "account" },
          },
          {
            title: "连接与集成",
            to: "/settings",
            search: { section: "integrations" },
          },
          {
            title: "偏好与代理",
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
                tabItem("/admin/data-ai", "sources", "数据来源"),
                tabItem("/admin/data-ai", "model", "模型"),
                tabItem("/admin/data-ai", "bot", "内置机器人"),
                tabItem("/admin/data-ai", "im", "IM 入口"),
                tabItem("/admin/data-ai", "mcp", "外部 AI 助手"),
                { title: "审计日志", to: "/admin/audit" },
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
          : item.search.section === search.section),
    ) ?? (path !== "/capacity" || !search.tab ? candidates[0] : undefined)

  const pageTitle =
    active?.title ??
    (path.startsWith("/employees/")
      ? "员工详情"
      : path === "/capacity"
        ? "需求与容量"
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
      collab: { to: assistant, badge: unread + pending },
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
  onNavigate,
}: Pick<
  ReturnType<typeof useWorkspaceNavigation>,
  "space" | "groups" | "active"
> & {
  onNavigate?: () => void
}) {
  return (
    <nav
      aria-label="空间导航"
      className="min-h-0 flex-1 overflow-y-auto px-3 pb-4"
    >
      <p className="px-2 py-4 text-lg font-semibold tracking-tight">
        {spaceTitles[space]}
      </p>
      {groups
        .filter((group) => group.items.length)
        .map(({ title, items }) => (
          <div className="mb-5" key={title ?? space}>
            {title && (
              <p className="mb-1 px-2 text-xs font-medium text-muted-foreground">
                {title}
              </p>
            )}
            <ul className="space-y-0.5">
              {items.map((item) => (
                <li key={item.title}>
                  <Link
                    to={item.to}
                    search={item.search}
                    activeOptions={{ exact: true }}
                    aria-current={item === active ? "page" : undefined}
                    onClick={onNavigate}
                    className="flex min-h-9 items-center gap-2 rounded-md border-l-2 border-transparent px-2.5 py-1 text-sm hover:bg-sidebar-accent focus-visible:outline-2 focus-visible:outline-ring aria-[current=page]:border-primary aria-[current=page]:bg-primary/10 aria-[current=page]:font-medium aria-[current=page]:text-foreground"
                  >
                    <span>{item.title}</span>
                    {!!item.badge && (
                      <span className="ml-auto rounded-full bg-primary/10 px-1.5 text-xs tabular-nums text-primary">
                        {item.badge}
                      </span>
                    )}
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        ))}
    </nav>
  )
}
