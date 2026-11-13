import { useQuery } from "@tanstack/react-query"
import {
  createFileRoute,
  Link,
  Navigate,
  useNavigate,
} from "@tanstack/react-router"
import type { ColumnDef } from "@tanstack/react-table"
import { useEffect, useState } from "react"
import { type EmployeeSummary, EmployeesService, OrgsService } from "@/client"
import { EmployeesAdminList } from "@/components/Admin/EmployeesAdminList"
import {
  DataTable,
  type DataTableFeatures,
} from "@/components/Common/DataTable"
import { publishedLabel } from "@/components/Profile/EmployeeSummary"
import { initialStatusLabel } from "@/components/Profile/initialStatus"
import { OrgState } from "@/components/Profile/OrgState"
import { StatusBadge } from "@/components/Profile/StatusBadge"
import {
  employeeFieldLabels,
  tenureLabel,
} from "@/components/Roster/EmployeeFields"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useOrg } from "@/hooks/useOrg"

export const Route = createFileRoute("/_layout/employees/")({
  component: EmployeesPage,
  validateSearch: (search: Record<string, unknown>): { filter?: string } => ({
    filter: ["appeals", "identities", "roles", "account-only"].includes(
      String(search.filter),
    )
      ? String(search.filter)
      : undefined,
  }),
  head: () => ({ meta: [{ title: "员工 - 员工画像平台" }] }),
})

const columns: ColumnDef<DataTableFeatures, EmployeeSummary>[] = [
  {
    accessorKey: "display_name",
    header: "姓名",
    cell: ({ row }) => (
      <Link
        className="font-medium hover:underline"
        to="/employees/$employeeId"
        params={{ employeeId: row.original.id }}
      >
        {row.original.display_name}
      </Link>
    ),
  },
  {
    accessorKey: "title",
    header: "职位",
    cell: ({ row }) => row.original.title ?? "—",
  },
  {
    id: "department",
    header: "部门",
    cell: ({ row }) => row.original.department?.name ?? "—",
  },
  ...(["fill_status", "inference_status", "review_status"] as const).map(
    (kind, i) => ({
      id: kind,
      header: ["填写进度", "初始画像", "审核状态"][i],
      cell: ({ row }: { row: { original: EmployeeSummary } }) =>
        kind === "inference_status" ? (
          initialStatusLabel(row.original.progress?.initial_profile_status)
        ) : (
          <StatusBadge kind={kind} value={row.original.progress?.[kind]} />
        ),
    }),
  ),
  {
    id: "published",
    header: "最近发布版本",
    cell: ({ row }) => (
      <span className="whitespace-nowrap text-muted-foreground">
        {publishedLabel(row.original.latest_published)}
      </span>
    ),
  },
]

function EmployeesPage() {
  const { org, roles } = useOrg()
  const { filter } = Route.useSearch()
  if (!org) return <OrgState />
  if (!roles.includes("org_admin") && !roles.includes("dept_manager"))
    return <Navigate to="/" replace />
  return roles.includes("org_admin") ? (
    <EmployeesAdminList key={org.id} filter={filter} />
  ) : (
    <EmployeesList key={org.id} orgId={org.id} />
  )
}

function EmployeesList({ orgId }: { orgId: string }) {
  const { org, roles, managedDepartmentIds } = useOrg()
  const navigate = useNavigate()
  const [department, setDepartment] = useState("all")
  const [search, setSearch] = useState("")
  const [q, setQ] = useState("")
  const [pagination, setPagination] = useState({ pageIndex: 0, pageSize: 25 })
  useEffect(() => {
    const timeout = setTimeout(() => {
      setQ(search.trim())
      setPagination((value) => ({ ...value, pageIndex: 0 }))
    }, 300)
    return () => clearTimeout(timeout)
  }, [search])
  const departments = useQuery({
    queryKey: ["departments", orgId],
    queryFn: async () =>
      (await OrgsService.readDepartments({ path: { org_id: orgId } })).data,
  })
  const employees = useQuery({
    queryKey: ["employees", orgId, department, q, pagination],
    queryFn: async () =>
      (
        await EmployeesService.readEmployees({
          path: { org_id: orgId },
          query: {
            department_id: department === "all" ? undefined : department,
            q: q || undefined,
            skip: pagination.pageIndex * pagination.pageSize,
            limit: pagination.pageSize,
          },
        })
      ).data,
  })
  const options =
    departments.data?.data.filter(
      (item) =>
        roles.includes("org_admin") || managedDepartmentIds.includes(item.id),
    ) ?? []
  return (
    <div className="flex flex-col gap-6">
      <div className="border-l-4 border-primary pl-4">
        <p className="mb-1 text-xs tracking-widest text-muted-foreground">
          组织 · 员工资源
        </p>
        <h1 className="text-3xl font-semibold tracking-tight">员工</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          了解工作经历与画像进度，让每项判断都有事实可循。
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Input
          aria-label="搜索姓名"
          placeholder="搜索姓名…"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          className="max-w-xs"
        />
        <Select
          value={department}
          onValueChange={(value) => {
            setDepartment(value)
            setPagination((old) => ({ ...old, pageIndex: 0 }))
          }}
        >
          <SelectTrigger aria-label="部门筛选" className="w-52">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部可见部门</SelectItem>
            {options.map((item) => (
              <SelectItem key={item.id} value={item.id}>
                {item.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <span className="ml-auto text-sm text-muted-foreground">
          共 {employees.data?.count ?? "—"} 名员工
        </span>
      </div>
      {departments.isError && (
        <div role="alert">
          无法加载部门筛选。
          <Button variant="outline" onClick={() => departments.refetch()}>
            重试
          </Button>
        </div>
      )}
      {employees.isPending ? (
        <p role="status">正在加载员工…</p>
      ) : employees.isError ? (
        <div role="alert">
          无法加载员工列表。
          <Button variant="outline" onClick={() => employees.refetch()}>
            重试
          </Button>
        </div>
      ) : (
        <>
          <section className="space-y-3 sm:hidden" aria-label="团队员工卡片">
            {employees.data.data.length === 0 && <p>暂无员工。</p>}
            {employees.data.data.map((employee) => (
              <article
                key={employee.id}
                aria-label={employee.display_name}
                className="rounded-xl border p-4 space-y-3 break-words"
              >
                <Link
                  className="font-semibold text-primary hover:underline"
                  to="/employees/$employeeId"
                  params={{ employeeId: employee.id }}
                >
                  {employee.display_name}
                </Link>
                <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 text-sm">
                  <dt className="text-muted-foreground">部门</dt>
                  <dd>{employee.department?.name ?? "未分配部门"}</dd>
                  <dt className="text-muted-foreground">初始画像</dt>
                  <dd>
                    {initialStatusLabel(
                      employee.progress?.initial_profile_status,
                    )}
                  </dd>
                  <dt className="text-muted-foreground">审核状态</dt>
                  <dd>
                    <StatusBadge
                      kind="review_status"
                      value={employee.progress?.review_status}
                    />
                  </dd>
                </dl>
              </article>
            ))}
            {employees.data.count > pagination.pageSize && (
              <div className="flex items-center justify-between gap-2">
                <Button
                  variant="outline"
                  disabled={pagination.pageIndex === 0}
                  onClick={() =>
                    setPagination((old) => ({
                      ...old,
                      pageIndex: old.pageIndex - 1,
                    }))
                  }
                >
                  上一页
                </Button>
                <span className="text-sm">
                  第 {pagination.pageIndex + 1} 页
                </span>
                <Button
                  variant="outline"
                  disabled={
                    (pagination.pageIndex + 1) * pagination.pageSize >=
                    employees.data.count
                  }
                  onClick={() =>
                    setPagination((old) => ({
                      ...old,
                      pageIndex: old.pageIndex + 1,
                    }))
                  }
                >
                  下一页
                </Button>
              </div>
            )}
          </section>
          <div className="hidden sm:block overflow-hidden rounded-xl border">
            <DataTable
              columns={[
                ...columns,
                ...(org?.employee_fields ?? []).map((field) => ({
                  id: field,
                  header:
                    field === "hire_date"
                      ? "入职年限"
                      : employeeFieldLabels[field],
                  cell: ({ row }: { row: { original: EmployeeSummary } }) =>
                    field === "hire_date" && row.original.hire_date
                      ? tenureLabel(row.original.hire_date)
                      : (row.original[field] ?? "—"),
                })),
              ]}
              data={employees.data.data}
              pagination={pagination}
              onPaginationChange={setPagination}
              rowCount={employees.data.count}
              onRowClick={(employee) =>
                navigate({
                  to: "/employees/$employeeId",
                  params: { employeeId: employee.id },
                })
              }
            />
          </div>
        </>
      )}
    </div>
  )
}
