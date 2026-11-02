import type {
  ColumnDef,
  OnChangeFn,
  PaginationState,
} from "@tanstack/react-table"
import type { EmployeeAccount } from "@/client"
import {
  DataTable,
  type DataTableFeatures,
} from "@/components/Common/DataTable"
import { Badge } from "@/components/ui/badge"
import { Checkbox } from "@/components/ui/checkbox"
import { accountLabels, employmentLabels, roleLabels } from "./labels"

export function AccountTable({
  data,
  count,
  departments,
  selected,
  onSelect,
  pagination,
  onPaginationChange,
}: {
  data: EmployeeAccount[]
  count: number
  departments: Map<string, string>
  selected: string[]
  onSelect: (id: string, checked: boolean) => void
  pagination: PaginationState
  onPaginationChange: OnChangeFn<PaginationState>
}) {
  const columns: ColumnDef<DataTableFeatures, EmployeeAccount>[] = [
    {
      id: "select",
      header: "选择",
      cell: ({ row: { original: e } }) => (
        <Checkbox
          aria-label={`选择 ${e.employee_code ?? e.display_name}`}
          checked={selected.includes(e.id)}
          disabled={
            e.employment_status !== "active" ||
            (!!e.account && e.account.status !== "pending_first_login")
          }
          onCheckedChange={(checked) => onSelect(e.id, checked === true)}
        />
      ),
    },
    { accessorKey: "employee_code", header: "工号" },
    { accessorKey: "display_name", header: "姓名" },
    {
      id: "department",
      header: "部门",
      cell: ({ row }) =>
        departments.get(row.original.department?.id ?? "") ?? "—",
    },
    {
      id: "employment",
      header: "在职状态",
      cell: ({ row }) => employmentLabels[row.original.employment_status],
    },
    {
      id: "status",
      header: "账号状态",
      cell: ({ row }) => (
        <Badge variant="secondary">
          {accountLabels[row.original.account?.status ?? "none"]}
        </Badge>
      ),
    },
    {
      id: "username",
      header: "登录名",
      cell: ({ row }) => row.original.account?.username ?? "—",
    },
    {
      id: "last_login",
      header: "最近登录",
      cell: ({ row }) =>
        row.original.account?.last_login_at
          ? new Date(row.original.account.last_login_at).toLocaleString("zh-CN")
          : "—",
    },
    {
      id: "roles",
      header: "角色",
      cell: ({ row }) =>
        row.original.roles
          .map(
            (role) =>
              `${roleLabels[role.role]}${role.department ? `（${departments.get(role.department.id) ?? role.department.name}）` : ""}`,
          )
          .join("、") || "—",
    },
    { id: "actions", header: "操作", cell: () => "—" },
  ]
  return (
    <div className="overflow-x-auto rounded-xl border">
      <DataTable
        columns={columns}
        data={data}
        rowCount={count}
        pagination={pagination}
        onPaginationChange={onPaginationChange}
      />
    </div>
  )
}
