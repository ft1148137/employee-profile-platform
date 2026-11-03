import { useQueryClient } from "@tanstack/react-query"
import type {
  CellContext,
  ColumnDef,
  OnChangeFn,
  PaginationState,
} from "@tanstack/react-table"
import { useCallback } from "react"
import { AccountsService, type EmployeeAccount } from "@/client"
import {
  DataTable,
  type DataTableFeatures,
} from "@/components/Common/DataTable"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import useAuth from "@/hooks/useAuth"
import { useOrg } from "@/hooks/useOrg"
import { ConfirmActionDialog } from "./ConfirmActionDialog"
import { accountLabels, employmentLabels, roleLabels } from "./labels"

export function AccountTable({
  data,
  count,
  departments,
  selected,
  onSelect,
  pagination,
  onPaginationChange,
  onResetPassword,
}: {
  data: EmployeeAccount[]
  count: number
  departments: Map<string, string>
  selected: string[]
  onSelect: (id: string, checked: boolean) => void
  pagination: PaginationState
  onPaginationChange: OnChangeFn<PaginationState>
  onResetPassword: (employee: EmployeeAccount) => void
}) {
  const actions = useCallback(
    ({ row }: CellContext<DataTableFeatures, EmployeeAccount>) => (
      <AccountActions
        employee={row.original}
        onResetPassword={onResetPassword}
      />
    ),
    [onResetPassword],
  )
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
    {
      id: "actions",
      header: "操作",
      cell: actions,
    },
  ]
  return (
    <div className="overflow-x-auto rounded-xl border">
      <DataTable
        columns={columns}
        data={data}
        getRowId={(employee) => employee.id}
        rowCount={count}
        pagination={pagination}
        onPaginationChange={onPaginationChange}
      />
    </div>
  )
}

function AccountActions({
  employee: e,
  onResetPassword,
}: {
  employee: EmployeeAccount
  onResetPassword: (employee: EmployeeAccount) => void
}) {
  const { user } = useAuth()
  const { org, roles } = useOrg()
  const client = useQueryClient()
  const refresh = () => {
    client.invalidateQueries({ queryKey: ["accounts", org?.id] })
  }
  if (!org || !roles.includes("org_admin") || !e.account) return "—"
  if (e.account.user_id === user?.id) return "请在设置页修改自己的密码"
  const path = { org_id: org.id, user_id: e.account.user_id }
  const disabled = e.account.status === "disabled"
  return (
    <div className="flex gap-2">
      <Button variant="outline" size="sm" onClick={() => onResetPassword(e)}>
        重置密码
      </Button>
      <ConfirmActionDialog
        title={disabled ? "启用" : "停用"}
        description={
          disabled
            ? `确认启用 ${e.display_name} 的账号？`
            : `确认停用 ${e.display_name} 的账号？停用后这名员工不能登录，画像和证据保留。`
        }
        action={() =>
          disabled
            ? AccountsService.enableAccount({ path })
            : AccountsService.disableAccount({ path })
        }
        onSuccess={refresh}
      />
    </div>
  )
}
