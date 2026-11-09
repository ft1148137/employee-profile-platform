import { createFileRoute } from "@tanstack/react-router"
import { AdminTabs, tabSearch } from "@/components/Admin/AdminTabs"
import { ApprovalPolicy } from "@/components/Admin/ApprovalPolicy"
import { DraftingPage } from "@/components/Admin/pages/DraftingPage"
import { ProfileImportsPage } from "@/components/Admin/pages/ProfileImportsPage"
import { UpdateSettings } from "@/components/Admin/UpdateSettings"
import { CapabilityDictionary } from "@/components/Setup/CapabilityDictionary"
import { useOrg } from "@/hooks/useOrg"

function Approval() {
  const { org } = useOrg()
  return org && <ApprovalPolicy key={org.id} orgId={org.id} />
}

function Reminders() {
  const { org } = useOrg()
  return org && <UpdateSettings key={org.id} orgId={org.id} />
}

const tabs = [
  { value: "dictionary", title: "能力词典", component: CapabilityDictionary },
  { value: "approval", title: "审批规则", component: Approval },
  { value: "drafting", title: "自动起草设置", component: DraftingPage },
  { value: "reminders", title: "定期提醒", component: Reminders },
  {
    value: "profile-import",
    title: "批量导入画像",
    component: ProfileImportsPage,
  },
]

export const Route = createFileRoute("/_layout/admin/rules")({
  component: Rules,
  validateSearch: tabSearch,
  head: () => ({ meta: [{ title: "能力与规则 - 员工画像平台" }] }),
})

function Rules() {
  const { roles } = useOrg()
  const { tab } = Route.useSearch()
  const navigate = Route.useNavigate()
  const admin = roles.includes("org_admin")
  return (
    <AdminTabs
      title="能力与规则"
      description={
        admin
          ? "维护能力词典，设置审批要求、自动起草范围和更新提醒。"
          : "查看和导出企业能力词典。"
      }
      tabs={admin ? tabs : tabs.slice(0, 1)}
      tab={tab}
      adminOnly={false}
      onTabChange={(tab) => navigate({ search: { tab } })}
    />
  )
}
