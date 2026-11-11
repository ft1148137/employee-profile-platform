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
import {
  DataTable,
  type DataTableFeatures,
} from "@/components/Common/DataTable"
import { publishedLabel } from "@/components/Profile/EmployeeSummary"
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
  ...(
    [
      "fill_status",
      "inference_status",
      "review_status",
      "evidence_quality",
    ] as const
  ).map((kind, i) => ({
    id: kind,
    header: ["填写进度", "推断进度", "审核状态", "证据质量"][i],
    cell: ({ row }: { row: { original: EmployeeSummary } }) => (
      <StatusBadge kind={kind} value={row.original.progress?.[kind]} />
    ),
  })),
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
  if (!org) return <OrgState />
  if (!roles.includes("org_admin") && !roles.includes("dept_manager"))
    return <Navigate to="/" replace />
  return <EmployeesList key={org.id} orgId={org.id} />
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
        <div className="overflow-hidden rounded-xl border">
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
      )}
    </div>
  )
}
