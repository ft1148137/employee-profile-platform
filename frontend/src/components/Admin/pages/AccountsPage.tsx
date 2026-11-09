import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import {
  AccountsService,
  type EmployeeAccount,
  type EmploymentStatus,
  OrgsService,
} from "@/client"
import { AccountTable } from "@/components/Admin/AccountTable"
import { departmentOptions } from "@/components/Admin/departments"
import { IssueAccountsDialog } from "@/components/Admin/IssueAccountsDialog"
import {
  type AccountStatus,
  accountLabels,
  employmentLabels,
} from "@/components/Admin/labels"
import { PersonnelDialog } from "@/components/Admin/PersonnelDialog"
import { ResetPasswordDialog } from "@/components/Admin/ResetPasswordDialog"
import { OrgState } from "@/components/Profile/OrgState"
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
import { handleError } from "@/utils"

export function AccountsPage() {
  const { org } = useOrg()
  return org ? <AccountsList key={org.id} orgId={org.id} /> : <OrgState />
}

function AccountsList({ orgId }: { orgId: string }) {
  const client = useQueryClient()
  const { roles } = useOrg()
  const [department, setDepartment] = useState("all")
  const [status, setStatus] = useState<AccountStatus | "all">("all")
  const [employment, setEmployment] = useState<EmploymentStatus | "all">("all")
  const [search, setSearch] = useState("")
  const [q, setQ] = useState("")
  const [passwordTarget, setPasswordTarget] = useState<EmployeeAccount | null>(
    null,
  )
  const [selected, setSelected] = useState<string[]>([])
  const [pagination, setPagination] = useState({ pageIndex: 0, pageSize: 50 })
  function reset() {
    setSelected([])
    setPagination((old) => ({ ...old, pageIndex: 0 }))
  }
  useEffect(() => {
    setSelected([])
    const timer = setTimeout(() => {
      setSelected([])
      setQ(search.trim())
      setPagination((old) => ({ ...old, pageIndex: 0 }))
    }, 300)
    return () => clearTimeout(timer)
  }, [search])
  const departments = useQuery({
    queryKey: ["departments", orgId],
    queryFn: async () =>
      (await OrgsService.readDepartments({ path: { org_id: orgId } })).data,
  })
  const accounts = useQuery({
    queryKey: [
      "accounts",
      orgId,
      department,
      status,
      employment,
      q,
      pagination,
    ],
    queryFn: async () =>
      (
        await AccountsService.readAccounts({
          path: { org_id: orgId },
          query: {
            department_id: department === "all" ? undefined : department,
            status: status === "all" ? undefined : status,
            employment_status: employment === "all" ? undefined : employment,
            q: q || undefined,
            skip: pagination.pageIndex * pagination.pageSize,
            limit: pagination.pageSize,
          },
        })
      ).data,
    retry: false,
  })
  const options = departmentOptions(departments.data?.data ?? [])
  let error = ""
  if (accounts.error)
    handleError.call((message) => {
      error = message
    }, accounts.error)
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4 border-l-4 border-primary pl-4">
        <div>
          <p className="mb-1 text-xs tracking-widest text-muted-foreground">
            管理 · 账号发放
          </p>
          <h1 className="text-3xl font-semibold tracking-tight">员工与账号</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            按员工名单发放账号，初始密码由员工首次登录时修改。
          </p>
        </div>
        {roles.includes("org_admin") && (
          <IssueAccountsDialog
            orgId={orgId}
            employees={accounts.data?.data
              .filter((e) => selected.includes(e.id))
              .map((e) => ({
                id: e.id,
                employee_code: e.employee_code,
                display_name: e.display_name,
                status: e.account?.status ?? "none",
              }))}
            onIssued={() => {
              setSelected([])
              client.invalidateQueries({ queryKey: ["accounts", orgId] })
            }}
          />
        )}
      </div>
      {roles.includes("org_admin") && <PersonnelDialog orgId={orgId} />}
      {passwordTarget?.account && (
        <ResetPasswordDialog
          orgId={orgId}
          userId={passwordTarget.account.user_id}
          name={passwordTarget.display_name}
          open={true}
          onClose={() => {
            setPasswordTarget(null)
            client.invalidateQueries({ queryKey: ["accounts", orgId] })
          }}
        />
      )}
      <div className="flex flex-wrap items-center gap-3">
        <Input
          className="max-w-xs"
          aria-label="搜索姓名或工号"
          placeholder="搜索姓名或工号…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <AccountFilter
          title="部门"
          value={department}
          labels={Object.fromEntries(options.map((d) => [d.id, d.name]))}
          onChange={(value) => {
            setDepartment(value)
            reset()
          }}
        />
        <AccountFilter
          title="账号状态"
          value={status}
          labels={accountLabels}
          onChange={(value) => {
            setStatus(value)
            reset()
          }}
        />
        <AccountFilter
          title="在职状态"
          value={employment}
          labels={employmentLabels}
          onChange={(value) => {
            setEmployment(value)
            reset()
          }}
        />
        <span className="ml-auto text-sm text-muted-foreground">
          共 {accounts.data?.count ?? "—"} 名员工 · 已选 {selected.length} 人
        </span>
      </div>
      {departments.isError && (
        <div role="alert">
          无法加载部门路径。
          <Button variant="outline" onClick={() => departments.refetch()}>
            重试
          </Button>
        </div>
      )}
      {accounts.isPending ? (
        <p role="status">正在加载员工与账号…</p>
      ) : accounts.isError ? (
        <div role="alert">
          {error}
          <Button variant="outline" onClick={() => accounts.refetch()}>
            重试
          </Button>
        </div>
      ) : (
        <AccountTable
          data={accounts.data.data}
          onResetPassword={setPasswordTarget}
          count={accounts.data.count}
          departments={new Map(options.map((d) => [d.id, d.name]))}
          selected={selected}
          onSelect={(id, checked) =>
            setSelected((old) =>
              checked ? [...old, id] : old.filter((value) => value !== id),
            )
          }
          pagination={pagination}
          onPaginationChange={(updater) => {
            setSelected([])
            setPagination(updater)
          }}
        />
      )}
    </div>
  )
}

function AccountFilter<T extends string>({
  title,
  value,
  labels,
  onChange,
}: {
  title: string
  value: T | "all"
  labels: Record<T, string>
  onChange: (value: T | "all") => void
}) {
  const keys = Object.keys(labels) as T[]
  return (
    <Select
      value={value}
      onValueChange={(value) => {
        const key = keys.find((key) => key === value)
        if (value === "all" || key) onChange(key ?? "all")
      }}
    >
      <SelectTrigger aria-label={`${title}筛选`} className="w-48">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value="all">全部{title}</SelectItem>
        {keys.map((key) => (
          <SelectItem key={key} value={key}>
            {labels[key]}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
