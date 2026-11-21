import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import type {
  CellContext,
  ColumnDef,
  OnChangeFn,
  PaginationState,
} from "@tanstack/react-table"
import { type SyntheticEvent, useCallback, useState } from "react"
import {
  AccountsService,
  type EmployeeAccount,
  EmployeesService,
} from "@/client"
import {
  DataTable,
  type DataTableFeatures,
} from "@/components/Common/DataTable"
import { preventRowEnterDefault } from "@/components/Profile/EmployeeQuickPeekDrawer"
import { publishedLabel } from "@/components/Profile/EmployeeSummary"
import { initialStatusLabel } from "@/components/Profile/initialStatus"
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

function stopPortalEvent(event: SyntheticEvent<HTMLElement>) {
  // React bubbles events from account dialogs through the row that opened them.
  if (
    event.target instanceof Node &&
    !event.currentTarget.contains(event.target)
  )
    event.stopPropagation()
}

function RowAccountActions({
  employee,
  onResetPassword,
}: {
  employee: EmployeeAccount
  onResetPassword: (employee: EmployeeAccount) => void
}) {
  return (
    <fieldset
      className="contents"
      aria-label="账号操作"
      onClick={stopPortalEvent}
      onKeyDown={stopPortalEvent}
    >
      <AccountMenu
        employee={employee}
        onResetPassword={() => onResetPassword(employee)}
        issuable={employee}
      />
    </fieldset>
  )
}

export function AccountTable({
  data,
  selected,
  onSelect,
  count,
  departments,
  pagination,
  onPaginationChange,
  onResetPassword,
  onPreview,
}: {
  selected: string[]
  onSelect: (id: string, checked: boolean) => void
  data: EmployeeAccount[]
  count: number
  departments: Map<string, string>
  pagination: PaginationState
  onPaginationChange: OnChangeFn<PaginationState>
  onResetPassword: (employee: EmployeeAccount) => void
  onPreview: (employee: EmployeeAccount) => void
}) {
  const { org } = useOrg()
  const actions = useCallback(
    ({ row }: CellContext<DataTableFeatures, EmployeeAccount>) => (
      <RowAccountActions
        employee={row.original}
        onResetPassword={onResetPassword}
      />
    ),
    [onResetPassword],
  )
  function selection(employee: EmployeeAccount) {
    return (
      <Checkbox
        aria-label={`选择 ${employee.employee_code ?? employee.display_name}`}
        checked={selected.includes(employee.id)}
        disabled={
          employee.employment_status !== "active" ||
          (!!employee.account &&
            employee.account.status !== "pending_first_login")
        }
        onCheckedChange={(checked) => onSelect(employee.id, checked === true)}
      />
    )
  }
  function employeeLink(employee: EmployeeAccount) {
    return (
      <Link
        className="font-medium text-primary hover:underline"
        to="/employees/$employeeId"
        params={{ employeeId: employee.id }}
      >
        {employee.display_name}
      </Link>
    )
  }
  const columns: ColumnDef<DataTableFeatures, EmployeeAccount>[] = [
    {
      id: "select",
      header: "选择",
      cell: ({ row }) => selection(row.original),
    },
    { accessorKey: "employee_code", header: "工号" },
    {
      accessorKey: "display_name",
      header: "姓名",
      cell: ({ row }) => employeeLink(row.original),
    },
    {
      id: "actions",
      header: "操作",
      cell: actions,
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
          <div className="flex gap-1">
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
      ["fill_status", "inference_status", "review_status", "published"] as const
    ).map((kind, index) => ({
      id: kind,
      header: ["填写进度", "初始画像", "审批状态", "最近发布版本"][index],
      cell: ({ row }: { row: { original: EmployeeAccount } }) =>
        org ? (
          <EmployeeProgress
            orgId={org.id}
            employeeId={row.original.id}
            kind={kind}
          />
        ) : (
          "—"
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
  ]
  return (
    <div className="min-w-0">
      <section aria-label="员工卡片" className="space-y-2 py-2 sm:hidden">
        {data.length === 0 && (
          <p className="p-3 text-sm text-muted-foreground">暂无数据</p>
        )}
        {data.map((employee) => (
          <article
            key={employee.id}
            className="space-y-2 rounded-lg border border-surface-border bg-surface p-3 wrap-anywhere"
          >
            <div className="flex items-start gap-2">
              <div className="flex size-8 shrink-0 items-center justify-center">
                {selection(employee)}
              </div>
              <div className="min-w-0 flex-1">
                {employeeLink(employee)}
                <p className="text-xs text-muted-foreground">
                  {[
                    employee.employee_code,
                    employee.title,
                    departments.get(employee.department?.id ?? ""),
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </p>
              </div>
              <RowAccountActions
                employee={employee}
                onResetPassword={onResetPassword}
              />
            </div>
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <Badge variant="secondary">
                {accountLabels[employee.account?.status ?? "none"]}
              </Badge>
              <span>{employmentLabels[employee.employment_status]}</span>
              {org && (
                <EmployeeProgress
                  orgId={org.id}
                  employeeId={employee.id}
                  kind="review_status"
                />
              )}
              <Button
                size="sm"
                variant="outline"
                className="ml-auto min-h-9"
                onClick={() => onPreview(employee)}
              >
                快速查看
              </Button>
            </div>
          </article>
        ))}
      </section>
      <div
        onKeyDownCapture={preventRowEnterDefault}
        className="[&>div]:rounded-t-none [&>div]:border-t-0 [&_th]:h-11 max-sm:[&_[data-slot=table-container]]:hidden"
      >
        <DataTable
          columns={columns}
          data={data}
          onRowClick={onPreview}
          getRowId={(employee) => employee.id}
          rowCount={count}
          pagination={pagination}
          onPaginationChange={onPaginationChange}
        />
      </div>
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
  if (e.account.user_id === user?.id)
    return "请在「设置 → 账号与安全」修改本人密码"
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
            : `停用后 ${e.display_name} 将无法登录，已有画像和记录会保留。确认停用吗？`
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
  orgId,
  employeeId,
  kind,
}: {
  orgId: string
  employeeId: string
  kind: "fill_status" | "inference_status" | "review_status" | "published"
}) {
  const summary = useQuery({
    queryKey: ["employee", orgId, employeeId],
    queryFn: async () =>
      (
        await EmployeesService.readEmployee({
          path: { org_id: orgId, employee_id: employeeId },
        })
      ).data,
  })
  if (summary.isPending) return "加载中…"
  if (summary.isError)
    return (
      <Button variant="ghost" size="sm" onClick={() => summary.refetch()}>
        重试进度
      </Button>
    )
  return kind === "published" ? (
    publishedLabel(summary.data.latest_published)
  ) : kind === "inference_status" ? (
    initialStatusLabel(summary.data.progress?.initial_profile_status)
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
  const name = e.display_name ?? e.account?.username ?? "员工"
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
                请在「设置 → 账号与安全」修改本人密码
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
              ? `确认启用 ${name} 的账号？`
              : `停用后 ${name} 将无法登录，已有画像和记录会保留。确认停用吗？`
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
