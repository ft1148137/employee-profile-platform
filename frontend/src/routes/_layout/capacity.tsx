import { createFileRoute } from "@tanstack/react-router"
import { tabSearch } from "@/components/Admin/AdminTabs"
import { IncomingDemands, OutgoingDemands } from "@/components/Capacity/Demands"
import { MyWorkload } from "@/components/Capacity/MyWorkload"
import {
  useCapacitySettings,
  workloadNotice,
} from "@/components/Capacity/shared"
import { TeamWorkload } from "@/components/Capacity/TeamWorkload"
import { OrgState } from "@/components/Profile/OrgState"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useOrg } from "@/hooks/useOrg"

export const Route = createFileRoute("/_layout/capacity")({
  component: CapacityPage,
  validateSearch: tabSearch,
  head: () => ({ meta: [{ title: "需求与容量 - 员工画像平台" }] }),
})

function CapacityPage() {
  const { org, roles, employeeId } = useOrg()
  const settings = useCapacitySettings(org?.id)
  const { tab } = Route.useSearch()
  const navigate = Route.useNavigate()
  if (!org) return <OrgState />
  if (settings.isPending) return <ProfileLoading />
  if (settings.isError) return <ProfileError retry={() => settings.refetch()} />
  if (!settings.data.enabled) return <p role="status">需求与容量模块未开启</p>
  const tabs = [
    ...(employeeId
      ? [{ value: "mine", title: "我的负荷", component: MyWorkload }]
      : []),
    ...(roles.includes("org_admin") || roles.includes("dept_manager")
      ? [{ value: "team", title: "团队负荷", component: TeamWorkload }]
      : []),
    ...(roles.includes("org_admin") ||
    roles.includes("dept_manager") ||
    (employeeId && settings.data.requester_policy === "everyone")
      ? [{ value: "outgoing", title: "我提的需求", component: OutgoingDemands }]
      : []),
    ...(roles.includes("org_admin") || roles.includes("dept_manager")
      ? [{ value: "incoming", title: "收到的需求", component: IncomingDemands }]
      : []),
  ]
  if (!tabs.length)
    return <p role="alert">需要员工、部门经理或企业管理员身份。</p>
  return (
    <div className="min-w-0 space-y-6">
      <header>
        <h1 className="text-3xl font-semibold">需求与容量</h1>
        <p className="mt-3 text-sm text-muted-foreground">{workloadNotice}</p>
      </header>
      <Tabs
        key={org.id}
        value={tabs.some((item) => item.value === tab) ? tab : tabs[0].value}
        onValueChange={(tab) => navigate({ search: { tab } })}
      >
        <TabsList aria-label="需求与容量" className="h-auto flex-wrap">
          {tabs.map((item) => (
            <TabsTrigger key={item.value} value={item.value}>
              {item.title}
            </TabsTrigger>
          ))}
        </TabsList>
        {tabs.map((item) => (
          <TabsContent key={item.value} value={item.value}>
            <item.component orgId={org.id} />
          </TabsContent>
        ))}
      </Tabs>
    </div>
  )
}
