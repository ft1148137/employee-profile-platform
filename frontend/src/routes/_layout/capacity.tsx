import { useIsMutating } from "@tanstack/react-query"
import { createFileRoute, Link, redirect } from "@tanstack/react-router"
import { FolderOpen, FolderPlus, Plus } from "lucide-react"
import { type ReactNode, useState } from "react"
import { tabSearch } from "@/components/Admin/AdminTabs"
import { BorrowRequests } from "@/components/Capacity/BorrowRequests"
import {
  DemandList,
  IncomingDemands,
  OutgoingDemands,
} from "@/components/Capacity/Demands"
import { DemandsCreate } from "@/components/Capacity/DemandsCreate"
import { MyWorkload } from "@/components/Capacity/MyWorkload"
import {
  type PlanningView,
  PlanningViewToggle,
} from "@/components/Capacity/PlanningWorkbench"
import { ProjectCreate } from "@/components/Capacity/ProjectCreate"
import { ProjectList, Projects } from "@/components/Capacity/ProjectDetail"
import {
  useCapacityDisabled,
  useCapacitySettings,
} from "@/components/Capacity/shared"
import { TeamWorkload } from "@/components/Capacity/TeamWorkload"
import { TopbarActions } from "@/components/Common/TopbarSlot"
import { SideDrawer } from "@/components/Common/WorkbenchPrimitives"
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
  if (!org) return <OrgState />
  if (disabled || settings.data?.enabled === false)
    return <p role="status">规划与负荷功能未开启，如需使用请联系企业管理员。</p>
  if (settings.isPending) return <ProfileLoading />
  if (settings.isError) return <ProfileError retry={() => settings.refetch()} />
  const manages = roles.includes("org_admin") || roles.includes("dept_manager")
  const canRequest =
    manages || (!!employeeId && settings.data.requester_policy === "everyone")
  return (
    <CapacityWorkspace
      key={org.id}
      orgId={org.id}
      manages={manages}
      hasEmployee={!!employeeId}
      canRequest={canRequest}
    />
  )
}

function CapacityWorkspace({
  orgId,
  manages,
  hasEmployee,
  canRequest,
}: {
  orgId: string
  manages: boolean
  hasEmployee: boolean
  canRequest: boolean
}) {
  const { tab, demand, plans, project } = Route.useSearch()
  const [display, setDisplay] = useState<PlanningView>("board")
  const workbench =
    tab === "outgoing" || tab === "projects" || tab === "borrows"
  const requestActions = (
    <>
      {!workbench && (
        <Button asChild size="icon-sm" variant="ghost">
          <Link
            to="/capacity"
            search={{ tab: "outgoing" }}
            aria-label="需求与项目"
          >
            <FolderOpen aria-hidden="true" />
          </Link>
        </Button>
      )}
      {canRequest && tab !== "incoming" && (
        <>
          <CreateDrawer orgId={orgId} kind="outgoing" />
          <CreateDrawer orgId={orgId} kind="projects" />
        </>
      )}
    </>
  )
  let title = "规划"
  let view: ReactNode
  let actions: ReactNode = (
    <TopbarActions>
      {((tab === "outgoing" && !demand) || (manages && tab === "borrows")) && (
        <PlanningViewToggle view={display} onChange={setDisplay} />
      )}
      {requestActions}
    </TopbarActions>
  )
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
    view =
      tab === "projects" ? (
        <ProjectList orgId={orgId} />
      ) : (
        <DemandList orgId={orgId} view={display} canRequest={canRequest} />
      )
  } else if (manages && tab === "borrows") {
    title = "借调请求"
    view = <BorrowRequests orgId={orgId} view={display} />
  } else if (manages && (tab === "team" || !hasEmployee)) {
    title = "团队负荷"
    view = <TeamWorkload orgId={orgId} />
    actions = <TopbarActions>{requestActions}</TopbarActions>
  } else if (hasEmployee) {
    title = "我的负荷"
    view = <MyWorkload orgId={orgId} actions={requestActions} />
    actions = null
  }
  return (
    <div
      data-workspace-page={title === "团队负荷" ? "" : undefined}
      className="min-w-0"
    >
      <h1 className="sr-only">{title}</h1>
      {actions}
      {workbench && !demand && !project && (
        <nav
          aria-label="需求工作台"
          className="mb-3 flex flex-wrap items-center gap-1"
        >
          {(
            [
              ["borrows", "借调看板"],
              ["outgoing", "已发出的需求"],
              ["projects", "项目列表"],
            ] as const
          )
            .filter(([tab]) => tab !== "borrows" || manages)
            .map(([target, label]) => (
              <Button
                key={target}
                size="sm"
                variant={tab === target ? "secondary" : "ghost"}
                asChild
              >
                <Link to="/capacity" search={{ tab: target }}>
                  {label}
                </Link>
              </Button>
            ))}
          <Button size="sm" variant="link" asChild>
            <Link to="/assistant">去 AI 助手</Link>
          </Button>
        </nav>
      )}
      {view}
    </div>
  )
}

function CreateDrawer({
  orgId,
  kind,
}: {
  orgId: string
  kind: "outgoing" | "projects"
}) {
  const [open, setOpen] = useState(false)
  const navigate = Route.useNavigate()
  const saving =
    useIsMutating({ mutationKey: ["capacity", orgId, "create", kind] }) > 0
  const isProject = kind === "projects"
  const Form = isProject ? ProjectCreate : DemandsCreate
  const Icon = isProject ? FolderPlus : Plus
  return (
    <SideDrawer
      title={isProject ? "新建项目需求" : "新建协作需求"}
      open={open}
      onOpenChange={(open) => {
        if (!saving) setOpen(open)
      }}
      className="sm:max-w-lg"
      trigger={
        <Button size="sm" variant={isProject ? "outline" : "default"}>
          <Icon aria-hidden="true" />
          <span className="sr-only lg:not-sr-only">
            {isProject ? "新建项目" : "新建需求"}
          </span>
        </Button>
      }
    >
      <Form
        orgId={orgId}
        back={() => setOpen(false)}
        created={(id) => {
          setOpen(false)
          void navigate({
            search: isProject
              ? { tab: kind, project: id }
              : { tab: kind, demand: id },
          })
        }}
      />
    </SideDrawer>
  )
}
