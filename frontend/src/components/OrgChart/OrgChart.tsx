import { useQuery } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import { ChevronDown, ChevronRight, Network } from "lucide-react"
import { useState } from "react"
import {
  EmployeesService,
  type OrgChartDepartment,
  OrgsService,
} from "@/client"
import { OrgState } from "@/components/Profile/OrgState"
import { ProfileError, ProfileLoading } from "@/components/Profile/QueryState"
import { Button } from "@/components/ui/button"
import { useOrg } from "@/hooks/useOrg"
import { cn } from "@/lib/utils"

export function OrgChart() {
  const { org, roles } = useOrg()
  if (!org) return <OrgState />
  if (!roles.some((role) => role === "org_admin" || role === "dept_manager"))
    return <p role="alert">需要企业管理员或部门经理权限</p>
  return <Chart key={org.id} orgId={org.id} />
}

function Chart({ orgId }: { orgId: string }) {
  const [selected, setSelected] = useState<string | null>(null)
  const chart = useQuery({
    queryKey: ["org-chart", orgId],
    queryFn: async () =>
      (await OrgsService.readOrgChart({ path: { org_id: orgId } })).data,
  })
  if (chart.isPending) return <ProfileLoading />
  if (chart.isError) return <ProfileError retry={() => chart.refetch()} />
  const departments = chart.data.departments
  if (!departments.length)
    return (
      <p className="py-12 text-center text-muted-foreground">暂无可见部门</p>
    )
  const children = new Map<string | null, OrgChartDepartment[]>()
  for (const department of departments) {
    const parent = department.parent_department_id
    children.set(parent, [...(children.get(parent) ?? []), department])
  }
  const department = departments.find((item) => item.id === selected)
  return (
    <div className="grid min-w-0 gap-5 xl:grid-cols-[minmax(0,1fr)_22rem]">
      <section
        aria-label="部门层级"
        className="min-w-0 overflow-hidden rounded-xl border"
      >
        <header className="flex items-center gap-3 border-b bg-muted/40 p-4">
          <Network className="size-5 text-primary" />
          <div>
            <h2 className="font-semibold">组织架构</h2>
            <p className="mt-1 text-xs text-muted-foreground">
              {departments.length} 个可见部门 · 人数仅统计在职员工
            </p>
          </div>
        </header>
        <div className="overflow-x-auto p-5">
          <ul className="w-max min-w-full space-y-4">
            {(children.get(null) ?? []).map((node) => (
              <DepartmentNode
                key={node.id}
                node={node}
                childrenByParent={children}
                depth={0}
                selected={selected}
                onSelect={setSelected}
              />
            ))}
          </ul>
        </div>
      </section>
      <aside
        aria-label="部门人员"
        className="min-w-0 self-start rounded-xl border"
      >
        {department ? (
          <DepartmentPeople
            key={department.id}
            orgId={orgId}
            department={department}
          />
        ) : (
          <div className="px-6 py-16 text-center">
            <h2 className="font-semibold">查看部门人员</h2>
            <p className="mt-2 text-sm text-muted-foreground">
              选择左侧部门，查看职位和汇报上级。
            </p>
          </div>
        )}
      </aside>
    </div>
  )
}

function DepartmentNode({
  node,
  childrenByParent,
  depth,
  selected,
  onSelect,
}: {
  node: OrgChartDepartment
  childrenByParent: Map<string | null, OrgChartDepartment[]>
  depth: number
  selected: string | null
  onSelect: (id: string) => void
}) {
  const [expanded, setExpanded] = useState(depth < 1)
  const children = childrenByParent.get(node.id) ?? []
  return (
    <li className="relative">
      <div
        className={cn(
          "w-64 overflow-hidden rounded-lg border bg-background shadow-xs transition-colors",
          selected === node.id && "border-primary ring-1 ring-primary",
        )}
      >
        <button
          type="button"
          aria-pressed={selected === node.id}
          aria-label={`查看${node.name}人员`}
          onClick={() => onSelect(node.id)}
          className="w-full p-4 text-left hover:bg-muted/40 focus-visible:outline-2 focus-visible:outline-primary"
        >
          <span className="block font-semibold">{node.name}</span>
          <span className="mt-2 block text-xs text-muted-foreground">
            经理：
            {node.managers?.map((manager) => manager.display_name).join("、") ||
              "未设置"}
          </span>
          <span className="mt-3 flex items-baseline gap-2 text-xs text-muted-foreground">
            本部门{" "}
            <strong className="text-lg tabular-nums text-foreground">
              {node.direct_count}
            </strong>
            <span className="ml-auto">
              含下级{" "}
              <strong className="text-foreground">{node.total_count}</strong> 人
            </span>
          </span>
        </button>
        {children.length > 0 && (
          <button
            type="button"
            aria-expanded={expanded}
            aria-controls={`departments-${node.id}`}
            aria-label={`${expanded ? "折叠" : "展开"}${node.name}下级部门`}
            onClick={() => setExpanded(!expanded)}
            className="flex w-full items-center gap-1 border-t bg-muted/30 px-4 py-2 text-xs hover:bg-muted focus-visible:outline-2 focus-visible:outline-primary"
          >
            {expanded ? (
              <ChevronDown className="size-3" />
            ) : (
              <ChevronRight className="size-3" />
            )}
            {children.length} 个下级部门
          </button>
        )}
      </div>
      {children.length > 0 && (
        <ul
          id={`departments-${node.id}`}
          hidden={!expanded}
          className="ml-5 mt-4 space-y-4 border-l border-border pl-6 [&>li]:before:absolute [&>li]:before:-left-6 [&>li]:before:top-6 [&>li]:before:w-6 [&>li]:before:border-t [&>li]:before:border-border"
        >
          {children.map((child) => (
            <DepartmentNode
              key={child.id}
              node={child}
              childrenByParent={childrenByParent}
              depth={depth + 1}
              selected={selected}
              onSelect={onSelect}
            />
          ))}
        </ul>
      )}
    </li>
  )
}

function DepartmentPeople({
  orgId,
  department,
}: {
  orgId: string
  department: OrgChartDepartment
}) {
  const { roles } = useOrg()
  const [page, setPage] = useState(0)
  const employees = useQuery({
    queryKey: ["employees", orgId, "org-chart", department.id, page],
    queryFn: async () =>
      (
        await EmployeesService.readEmployees({
          path: { org_id: orgId },
          query: {
            department_id: department.id,
            employment_status: "active",
            skip: page * 25,
            limit: 25,
          },
        })
      ).data,
  })
  return (
    <>
      <header className="border-b p-4">
        <h2 className="font-semibold">{department.name} · 人员</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          本部门在职人员，不含下级部门
        </p>
      </header>
      {employees.isPending ? (
        <ProfileLoading />
      ) : employees.isError ? (
        <ProfileError retry={() => employees.refetch()} />
      ) : (
        <div className="p-4">
          {employees.data.count === 0 && (
            <p className="py-6 text-sm text-muted-foreground">
              本部门暂无在职人员
            </p>
          )}
          <ul className="divide-y">
            {employees.data.data.map((employee) => (
              <li key={employee.id} className="py-3 first:pt-0">
                <Link
                  to={
                    roles.includes("org_admin")
                      ? "/admin/people/$employeeId"
                      : "/employees/$employeeId"
                  }
                  params={{ employeeId: employee.id }}
                  className="font-medium text-primary hover:underline"
                >
                  {employee.display_name}
                </Link>
                <p className="mt-1 text-sm">{employee.title || "职位未填写"}</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  上级：
                  {employee.manager?.display_name ?? "未设置或不在可见范围"}
                </p>
              </li>
            ))}
          </ul>
          <div className="mt-4 flex items-center justify-between gap-2 border-t pt-4 text-xs text-muted-foreground">
            <span>
              共 {employees.data.count} 人 · 第 {page + 1} 页
            </span>
            <div className="flex gap-1">
              <Button
                variant="outline"
                size="sm"
                disabled={page === 0}
                onClick={() => setPage(page - 1)}
              >
                上一页
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={(page + 1) * 25 >= employees.data.count}
                onClick={() => setPage(page + 1)}
              >
                下一页
              </Button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
