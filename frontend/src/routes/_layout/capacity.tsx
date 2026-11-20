import { createFileRoute, Link, redirect } from "@tanstack/react-router"
import { type ReactNode, useState } from "react"
import { tabSearch } from "@/components/Admin/AdminTabs"
import { BorrowRequests } from "@/components/Capacity/BorrowRequests"
import { IncomingDemands, OutgoingDemands } from "@/components/Capacity/Demands"
import { DemandsCreate } from "@/components/Capacity/DemandsCreate"
import { MyWorkload } from "@/components/Capacity/MyWorkload"
import { ProjectCreate } from "@/components/Capacity/ProjectCreate"
import { Projects } from "@/components/Capacity/ProjectDetail"
import {
  useCapacityDisabled,
  useCapacitySettings,
  workloadNotice,
} from "@/components/Capacity/shared"
import { TeamWorkload } from "@/components/Capacity/TeamWorkload"
import { OrgState } from "@/components/Profile/OrgState"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { Button } from "@/components/ui/button"
import { useOrg } from "@/hooks/useOrg"

export const Route = createFileRoute("/_layout/capacity")({
  component: CapacityPage,
  validateSearch: (search: Record<string, unknown>) => ({
    ...tabSearch(search),
    ...(typeof search.demand === "string" ? { demand: search.demand } : {}),
    ...(typeof search.project === "string" ? { project: search.project } : {}),
    ...(search.plans === "1" || search.plans === 1
      ? { plans: 1 as const }
      : {}),
  }),
  beforeLoad: ({ search }) => {
    const { tab, demand } = search
    if (tab === "assignments")
      throw redirect({
        to: "/capacity",
        search: { tab: "mine" },
        replace: true,
      })
    if (tab === "approvals" || (tab === "incoming" && !demand))
      throw redirect({
        to: "/reviews",
        search: { type: tab === "approvals" ? "workload" : "demand" },
        replace: true,
      })
  },
  head: () => ({ meta: [{ title: "规划 - 员工画像平台" }] }),
})

function CapacityPage() {
  const { org, roles, employeeId } = useOrg()
  const settings = useCapacitySettings(org?.id)
  const disabled = useCapacityDisabled(org?.id)
  const { tab, demand, plans, project } = Route.useSearch()
  if (!org) return <OrgState />
  if (disabled || settings.data?.enabled === false)
    return <p role="status">规划与负荷功能未开启，如需使用请联系企业管理员。</p>
  if (settings.isPending) return <ProfileLoading />
  if (settings.isError) return <ProfileError retry={() => settings.refetch()} />
  const manages = roles.includes("org_admin") || roles.includes("dept_manager")
  const canRequest =
    manages || (!!employeeId && settings.data.requester_policy === "everyone")
  const isPlanningView = !tab || ["mine", "team", "borrows"].includes(tab)
  const orgId = org.id
  let title = "规划"
  let view: ReactNode
  if (manages && tab === "incoming" && demand) {
    title = "协作需求"
    view = (
      <IncomingDemands orgId={orgId} demand={demand} plansOpen={plans === 1} />
    )
  } else if (tab === "outgoing" && demand) {
    title = "协作需求"
    view = <OutgoingDemands orgId={orgId} demand={demand} />
  } else if (tab === "projects" && project) {
    title = "项目需求"
    view = <Projects orgId={orgId} projectId={project} />
  } else if (tab === "outgoing" || tab === "projects") {
    title = tab === "projects" ? "项目需求" : "协作需求"
    view = (
      <RequestGuide key={tab} orgId={orgId} tab={tab} canRequest={canRequest} />
    )
  } else if (manages && tab === "borrows") {
    title = "借调请求"
    view = <BorrowRequests orgId={orgId} />
  } else if (manages && (tab === "team" || !employeeId)) {
    title = "团队负荷"
    view = <TeamWorkload orgId={orgId} />
  } else if (employeeId) {
    title = "我的负荷"
    view = <MyWorkload orgId={orgId} />
  }
  return (
    <div key={orgId} className="min-w-0 space-y-6">
      <header>
        <h1 className="text-3xl font-semibold">{title}</h1>
        <p className="mt-3 text-sm text-muted-foreground">{workloadNotice}</p>
        {canRequest && isPlanningView && (
          <div className="mt-4 flex flex-wrap gap-2">
            <Button variant="outline" asChild>
              <Link to="/capacity" search={{ tab: "outgoing" }}>
                新建需求
              </Link>
            </Button>
            <Button variant="outline" asChild>
              <Link to="/capacity" search={{ tab: "projects" }}>
                新建项目
              </Link>
            </Button>
          </div>
        )}
      </header>
      {view}
    </div>
  )
}

function RequestGuide({
  orgId,
  tab,
  canRequest,
}: {
  orgId: string
  tab: "outgoing" | "projects"
  canRequest: boolean
}) {
  const [creating, setCreating] = useState(false)
  const navigate = Route.useNavigate()
  const isProject = tab === "projects"
  const Form = isProject ? ProjectCreate : DemandsCreate
  if (creating && canRequest)
    return (
      <Form
        orgId={orgId}
        back={() => setCreating(false)}
        created={(id) => {
          setCreating(false)
          navigate({
            search: isProject ? { tab, project: id } : { tab, demand: id },
          })
        }}
      />
    )
  return (
    <section className="max-w-xl space-y-3 rounded-xl border p-5">
      <h2 className="text-lg font-semibold">
        {isProject ? "项目需求" : "部门需求"}
      </h2>
      <p className="text-sm text-muted-foreground">
        可在 AI 助手中快速发起并跟进进展，或直接通过表单新建。
      </p>
      <div className="flex flex-wrap gap-2">
        <Button asChild>
          <Link to="/assistant">去 AI 助手</Link>
        </Button>
        {canRequest && (
          <Button variant="outline" onClick={() => setCreating(true)}>
            {isProject ? "新建项目" : "新建需求"}
          </Button>
        )}
      </div>
    </section>
  )
}
