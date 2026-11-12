import { useQuery } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import { ChevronDown, ChevronRight, Network } from "lucide-react"
import { type ReactNode, useState } from "react"
import { type OrgChartPerson, OrgsService } from "@/client"
import { OrgState } from "@/components/Profile/OrgState"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Button } from "@/components/ui/button"
import { useOrg } from "@/hooks/useOrg"
import { cn } from "@/lib/utils"
import { DepartmentCapabilities } from "./DepartmentCapabilities"
import "./org-chart.css"

export function OrgChart() {
  const { org, roles } = useOrg()
  if (!org) return <OrgState />
  if (!roles.some((role) => role === "org_admin" || role === "dept_manager"))
    return <p role="alert">需要企业管理员或部门经理权限</p>
  return (
    <Chart key={org.id} orgId={org.id} canEdit={roles.includes("org_admin")} />
  )
}

// Only members of a cycle become roots; their other reports remain attached.
function reportingCycles(people: OrgChartPerson[]) {
  const managers = new Map(
    people.map((person) => [person.id, person.manager_id]),
  )
  const checked = new Set<string>()
  const cycles = new Set<string>()
  for (const person of people) {
    const path = new Map<string, number>()
    let id: string | null | undefined = person.id
    while (id && managers.has(id) && !checked.has(id)) {
      const start = path.get(id)
      if (start !== undefined) {
        for (const [member, index] of path)
          if (index >= start) cycles.add(member)
        break
      }
      path.set(id, path.size)
      id = managers.get(id)
    }
    for (const member of path.keys()) checked.add(member)
  }
  return cycles
}

function Chart({ orgId, canEdit }: { orgId: string; canEdit: boolean }) {
  const [selectedDepartment, setDepartment] = useState<string | null>(null)
  const [selectedPerson, setPerson] = useState<string | null>(null)
  const [collapsed, setCollapsed] = useState(new Set<string>())
  const chart = useQuery({
    queryKey: ["org-chart", orgId],
    queryFn: async () =>
      (await OrgsService.readOrgChart({ path: { org_id: orgId } })).data,
  })
  if (chart.isPending) return <ProfileLoading />
  if (chart.isError) return <ProfileError retry={() => chart.refetch()} />
  const { departments, people } = chart.data
  const department = departments.find((item) => item.id === selectedDepartment)
  const person = people.find((item) => item.id === selectedPerson)
  const cycles = reportingCycles(people)
  const children = new Map<string | null, string[]>()
  const addChild = (parent: string | null, id: string) =>
    children.set(parent, [...(children.get(parent) ?? []), id])
  let renderCard: (id: string) => ReactNode
  let label = "部门层级"
  if (person || department) {
    const visible = person
      ? people
      : people.filter((item) => item.department_id === department?.id)
    const byId = new Map(visible.map((item) => [item.id, item]))
    if (person) {
      label = "人员汇报关系"
      const chain = [person.id]
      let manager = person.manager_id
      while (manager && byId.has(manager) && !chain.includes(manager)) {
        chain.unshift(manager)
        manager = byId.get(manager)?.manager_id ?? null
      }
      chain.forEach((id, index) => {
        addChild(index ? chain[index - 1] : null, id)
      })
      for (const report of people)
        if (report.manager_id === person.id && !chain.includes(report.id))
          addChild(person.id, report.id)
    } else {
      label = "部门汇报关系"
      for (const item of visible)
        addChild(
          !cycles.has(item.id) && item.manager_id && byId.has(item.manager_id)
            ? item.manager_id
            : null,
          item.id,
        )
    }
    renderCard = (id) => {
      const item = byId.get(id)!
      return (
        <PersonCard
          person={item}
          selected={person?.id === id}
          cycle={cycles.has(id)}
          onSelect={() => setPerson(id)}
        />
      )
    }
  } else {
    const byId = new Map(departments.map((item) => [item.id, item]))
    for (const item of departments) addChild(item.parent_department_id, item.id)
    renderCard = (id) => {
      const item = byId.get(id)!
      const childCount = children.get(id)?.length ?? 0
      return (
        <div className="w-52 overflow-hidden rounded-xl border bg-card text-card-foreground shadow-sm">
          <button
            type="button"
            aria-label={`查看${item.name}人员`}
            onClick={() => setDepartment(id)}
            className="w-full p-4 text-left hover:bg-muted focus-visible:outline-2 focus-visible:outline-primary"
          >
            <span className="block font-semibold">{item.name}</span>
            <span className="mt-2 block text-xs text-muted-foreground">
              经理：
              {item.managers
                ?.map((manager) => manager.display_name)
                .join("、") || "未设置"}
            </span>
            <span className="mt-3 flex justify-between text-xs text-muted-foreground">
              <span>
                本部门{" "}
                <strong className="text-foreground">{item.direct_count}</strong>{" "}
                人
              </span>
              <span>
                含下级{" "}
                <strong className="text-foreground">{item.total_count}</strong>{" "}
                人
              </span>
            </span>
          </button>
          {childCount > 0 && (
            <button
              type="button"
              aria-expanded={!collapsed.has(id)}
              aria-controls={`org-children-${id}`}
              aria-label={`${collapsed.has(id) ? "展开" : "折叠"}${item.name}下级部门`}
              onClick={() =>
                setCollapsed((old) => {
                  const next = new Set(old)
                  if (next.has(id)) next.delete(id)
                  else next.add(id)
                  return next
                })
              }
              className="flex w-full items-center justify-center gap-1 border-t bg-muted/40 py-2 text-xs hover:bg-muted focus-visible:outline-2 focus-visible:outline-primary"
            >
              {collapsed.has(id) ? (
                <ChevronRight className="size-3" />
              ) : (
                <ChevronDown className="size-3" />
              )}
              {childCount} 个下级部门
            </button>
          )}
        </div>
      )
    }
  }
  return (
    <div className="min-w-0 space-y-5">
      <header className="flex items-center gap-3">
        <span className="rounded-xl bg-primary/10 p-3 text-primary">
          <Network className="size-6" />
        </span>
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">组织架构</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {departments.length} 个可见部门 · 人数仅统计在职员工
          </p>
        </div>
      </header>
      <nav
        aria-label="组织架构面包屑"
        className="flex flex-wrap items-center gap-2 text-sm"
      >
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            setDepartment(null)
            setPerson(null)
          }}
        >
          部门层级
        </Button>
        {department && (
          <>
            <ChevronRight className="size-4 text-muted-foreground" />
            <Button variant="ghost" size="sm" onClick={() => setPerson(null)}>
              {department.name}
            </Button>
          </>
        )}
        {person && (
          <>
            <ChevronRight className="size-4 text-muted-foreground" />
            <span aria-current="page">{person.display_name}</span>
          </>
        )}
      </nav>
      <section
        aria-label={label}
        className="overflow-hidden rounded-2xl border bg-muted/20"
      >
        <header className="flex items-center justify-between border-b bg-background px-5 py-3">
          <h2 className="font-medium">
            {person
              ? `${person.display_name} · 上级与直接下属`
              : department
                ? `${department.name} · 汇报关系`
                : "部门层级"}
          </h2>
          <span className="text-xs text-muted-foreground">
            {department && !person
              ? `共 ${department.direct_count} 人`
              : "只读 · 点击卡片查看"}
          </span>
        </header>
        <section
          aria-label="可滚动组织架构图"
          className="max-h-[calc(100dvh-20rem)] min-h-80 overflow-auto p-6 focus-visible:outline-2 focus-visible:outline-primary"
        >
          {children.size ? (
            <ChartTree
              childrenByParent={children}
              renderCard={renderCard}
              collapsed={person || department ? new Set() : collapsed}
            />
          ) : (
            <p className="py-20 text-center text-sm text-muted-foreground">
              {department ? "本部门暂无在职人员" : "暂无可见部门"}
            </p>
          )}
        </section>
      </section>
      {department && !person && (
        <DepartmentCapabilities
          key={department.id}
          orgId={orgId}
          departmentId={department.id}
          canEdit={canEdit}
        />
      )}
    </div>
  )
}

function PersonCard({
  person,
  selected,
  cycle,
  onSelect,
}: {
  person: OrgChartPerson
  selected: boolean
  cycle: boolean
  onSelect: () => void
}) {
  return (
    <div
      className={cn(
        "w-52 overflow-hidden rounded-xl border bg-card text-card-foreground shadow-sm",
        selected && "border-primary ring-2 ring-primary/30",
      )}
    >
      <button
        type="button"
        aria-label={`查看${person.display_name}汇报关系`}
        aria-pressed={selected}
        onClick={onSelect}
        className="flex w-full items-center gap-3 p-4 text-left hover:bg-muted focus-visible:outline-2 focus-visible:outline-primary"
      >
        <Avatar className="size-10">
          <AvatarFallback className="bg-primary/10 text-primary">
            {person.display_name.slice(0, 1)}
          </AvatarFallback>
        </Avatar>
        <span className="min-w-0">
          <span className="block font-semibold">{person.display_name}</span>
          <span className="mt-1 block text-xs text-muted-foreground">
            {person.title || "职位未填写"}
          </span>
        </span>
      </button>
      {cycle && (
        <p className="px-3 pb-3 text-xs text-destructive">汇报线有循环</p>
      )}
      {selected && (
        <Button
          asChild
          variant="ghost"
          className="w-full rounded-none border-t text-primary"
        >
          <Link to="/employees/$employeeId" params={{ employeeId: person.id }}>
            查看详情
          </Link>
        </Button>
      )}
    </div>
  )
}

function ChartTree({
  childrenByParent,
  renderCard,
  collapsed,
}: {
  childrenByParent: Map<string | null, string[]>
  renderCard: (id: string) => ReactNode
  collapsed: Set<string>
}) {
  function branch(id: string): ReactNode {
    const children = childrenByParent.get(id) ?? []
    return (
      <li key={id}>
        {renderCard(id)}
        {children.length > 0 && !collapsed.has(id) && (
          <>
            <span
              className="org-stem"
              data-testid="org-connector"
              aria-hidden="true"
            />
            <ul id={`org-children-${id}`}>{children.map(branch)}</ul>
          </>
        )}
      </li>
    )
  }
  return (
    <ul className="org-tree">
      {(childrenByParent.get(null) ?? []).map(branch)}
    </ul>
  )
}
