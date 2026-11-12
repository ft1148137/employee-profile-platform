import {
  createFileRoute,
  Outlet,
  redirect,
  useChildMatches,
} from "@tanstack/react-router"
import { AdminTabs, tabSearch } from "@/components/Admin/AdminTabs"
import { AccountsPage } from "@/components/Admin/pages/AccountsPage"
import { AppealsPage } from "@/components/Admin/pages/AppealsPage"
import { IdentitiesPage } from "@/components/Admin/pages/IdentitiesPage"
import { RolesPage } from "@/components/Admin/pages/RolesPage"
import { OrgChart } from "@/components/OrgChart/OrgChart"

const tabs = [
  { value: "accounts", title: "员工与账号", component: AccountsPage },
  { value: "org-chart", title: "组织架构", component: OrgChart },
  { value: "roles", title: "角色", component: RolesPage },
  { value: "identities", title: "Jira 账号匹配", component: IdentitiesPage },
  { value: "appeals", title: "争议", component: AppealsPage },
]

export const Route = createFileRoute("/_layout/admin/people")({
  component: People,
  validateSearch: tabSearch,
  beforeLoad: ({ search }) => {
    if (search.tab === "org-chart")
      throw redirect({ to: "/org-chart", replace: true })
    if (search.tab === "roster")
      throw redirect({ to: "/admin/assistant", replace: true })
  },
  head: () => ({ meta: [{ title: "人员 - 员工画像平台" }] }),
})

function People() {
  const { tab } = Route.useSearch()
  const navigate = Route.useNavigate()
  const children = useChildMatches()
  if (children.length) return <Outlet />
  return (
    <AdminTabs
      title="人员"
      description="管理员工名单、登录账号和角色，核对工作账号，处理争议。"
      tabs={tabs}
      tab={tab}
      onTabChange={(tab) => navigate({ search: { tab } })}
    />
  )
}
