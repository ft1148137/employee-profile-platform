import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Link, useNavigate } from "@tanstack/react-router"
import type {
  CellContext,
  ColumnDef,
  OnChangeFn,
  PaginationState,
} from "@tanstack/react-table"
import { useCallback, useState } from "react"
import {
  AccountsService,
  type EmployeeAccount,
  EmployeesService,
} from "@/client"
import {
  DataTable,
  type DataTableFeatures,
} from "@/components/Common/DataTable"
import { publishedLabel } from "@/components/Profile/EmployeeSummary"
import { StatusBadge } from "@/components/Profile/StatusBadge"
import {
  employeeFieldLabels,
  tenureLabel,
} from "@/components/Roster/EmployeeFields"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import useAuth from "@/hooks/useAuth"
import { useOrg } from "@/hooks/useOrg"
import { ConfirmActionDialog } from "./ConfirmActionDialog"
import { IssueAccountsDialog } from "./IssueAccountsDialog"
import { accountLabels, employmentLabels, roleLabels } from "./labels"

export function AccountTable({
  data,
  selected,
  onSelect,
  count,
  departments,
  pagination,
  onPaginationChange,
  onResetPassword,
}: {
  selected: string[]
  onSelect: (id: string, checked: boolean) => void
  data: EmployeeAccount[]
  count: number
  departments: Map<string, string>
  pagination: PaginationState
  onPaginationChange: OnChangeFn<PaginationState>
  onResetPassword: (employee: EmployeeAccount) => void
}) {
  const navigate = useNavigate()
  const { org } = useOrg()
  const actions = useCallback(
    ({ row }: CellContext<DataTableFeatures, EmployeeAccount>) => (
      <AccountMenu
        employee={row.original}
        onResetPassword={() => onResetPassword(row.original)}
        issuable={row.original}
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
    {
      accessorKey: "display_name",
      header: "姓名",
      cell: ({ row }) => (
        <Link
          className="text-primary underline underline-offset-4"
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
      id: "roles",
      header: "角色",
      cell: ({ row }) =>
        row.original.roles.length ? (
          <div className="flex flex-wrap gap-1">
            {row.original.roles.map((role) => (
              <Badge key={role.id} variant="outline">
                {roleLabels[role.role]}
                {role.department
                  ? `（${departments.get(role.department.id) ?? role.department.name}）`
                  : ""}
              </Badge>
            ))}
          </div>
        ) : (
          "—"
        ),
    },
    ...(
      [
        "fill_status",
        "inference_status",
        "review_status",
        "evidence_quality",
        "published",
      ] as const
    ).map((kind, index) => ({
      id: kind,
      header: ["填写进度", "推断进度", "审核状态", "证据质量", "最近发布版本"][
        index
      ],
      cell: ({ row }: { row: { original: EmployeeAccount } }) => (
        <EmployeeProgress employeeId={row.original.id} kind={kind} />
      ),
    })),
    ...(org?.employee_fields ?? []).map((field) => ({
      id: field,
      header: field === "hire_date" ? "入职年限" : employeeFieldLabels[field],
      cell: ({ row }: { row: { original: EmployeeAccount } }) =>
        field === "hire_date" && row.original.hire_date
          ? tenureLabel(row.original.hire_date)
          : (row.original[field] ?? "—"),
    })),
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
        onRowClick={(employee) =>
          navigate({
            to: "/employees/$employeeId",
            params: { employeeId: employee.id },
          })
        }
        getRowId={(employee) => employee.id}
        rowCount={count}
        pagination={pagination}
        onPaginationChange={onPaginationChange}
      />
    </div>
  )
}

export function AccountActions({
  employee: e,
  onResetPassword,
}: {
  employee: { display_name: string | null; account: EmployeeAccount["account"] }
  onResetPassword: () => void
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
      <Button variant="outline" size="sm" onClick={() => onResetPassword()}>
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

function EmployeeProgress({
  employeeId,
  kind,
}: {
  employeeId: string
  kind:
    | "fill_status"
    | "inference_status"
    | "review_status"
    | "evidence_quality"
    | "published"
}) {
  const { org } = useOrg()
  const summary = useQuery({
    queryKey: ["employee", org?.id, employeeId],
    queryFn: async () =>
      (
        await EmployeesService.readEmployee({
          path: { org_id: org!.id, employee_id: employeeId },
        })
      ).data,
  })
  if (summary.isPending) return "加载中…"
  if (summary.isError)
    return (
      <Button variant="ghost" onClick={() => summary.refetch()}>
        重试进度
      </Button>
    )
  return kind === "published" ? (
    publishedLabel(summary.data.latest_published)
  ) : (
    <StatusBadge kind={kind} value={summary.data.progress?.[kind]} />
  )
}

export function AccountMenu({
  employee: e,
  onResetPassword,
  issuable,
}: {
  employee: { display_name: string | null; account: EmployeeAccount["account"] }
  onResetPassword: () => void
  issuable?: EmployeeAccount
}) {
  const { org } = useOrg()
  const { user } = useAuth()
  const client = useQueryClient()
  const [confirm, setConfirm] = useState(false)
  const [issue, setIssue] = useState(false)
  if (!org) return null
  const disabled = e.account?.status === "disabled"
  const refresh = () =>
    client.invalidateQueries({ queryKey: ["accounts", org.id] })
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" size="sm">
            更多
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent onClick={(event) => event.stopPropagation()}>
          {issuable?.employment_status === "active" &&
            (!e.account || e.account.status === "pending_first_login") && (
              <DropdownMenuItem onSelect={() => setIssue(true)}>
                发放账号
              </DropdownMenuItem>
            )}
          {e.account &&
            (e.account.user_id === user?.id ? (
              <DropdownMenuItem disabled>
                请在设置页修改自己的密码
              </DropdownMenuItem>
            ) : (
              <>
                <DropdownMenuItem onSelect={onResetPassword}>
                  重置密码
                </DropdownMenuItem>
                <DropdownMenuItem onSelect={() => setConfirm(true)}>
                  {disabled ? "启用" : "停用"}
                </DropdownMenuItem>
              </>
            ))}
        </DropdownMenuContent>
      </DropdownMenu>
      {e.account && (
        <ConfirmActionDialog
          title={disabled ? "启用" : "停用"}
          description={
            disabled
              ? `确认启用 ${e.display_name} 的账号？`
              : `确认停用 ${e.display_name} 的账号？停用后这名员工不能登录，画像和证据保留。`
          }
          open={confirm}
          onOpenChange={setConfirm}
          action={() =>
            disabled
              ? AccountsService.enableAccount({
                  path: { org_id: org.id, user_id: e.account!.user_id },
                })
              : AccountsService.disableAccount({
                  path: { org_id: org.id, user_id: e.account!.user_id },
                })
          }
          onSuccess={refresh}
        />
      )}
      {issuable && (
        <IssueAccountsDialog
          orgId={org.id}
          open={issue}
          onOpenChange={setIssue}
          employees={[
            {
              id: issuable.id,
              employee_code: issuable.employee_code,
              display_name: issuable.display_name,
              status: e.account?.status ?? "none",
            },
          ]}
          onIssued={refresh}
        />
      )}
    </>
  )
}
